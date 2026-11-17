"""Offline CLIP category relevance. A similarity signal, never SKU verification."""
from pathlib import Path
import json
import re
import numpy as np
from PIL import Image,ImageOps

ROOT=Path(__file__).resolve().parents[1]
MODEL=ROOT/'models/clip-vit-base-patch32'


def ready():return all((MODEL/name).is_file() for name in ('manifest.json','tokenizer.json','onnx/text_model_quantized.onnx','onnx/vision_model_quantized.onnx'))


def category(text):
    rules=[('a guitar effects pedal',r'效果器'),('a microphone',r'麦克风|话筒|microphone'),
           ('a studio monitor loudspeaker',r'音箱|扬声器|监听音响'),('a wireless guitar transmitter',r'吉他.{0,8}无线|无线.{0,8}吉他'),
           ('an audio interface',r'声卡|音频接口'),('a music synthesizer',r'合成器'),('a MIDI music keyboard',r'键盘'),
           ('a drum machine',r'鼓机|节奏机'),('headphones',r'耳机|耳返'),('an audio direct box',r'DI盒|DI\b'),
           ('a multitrack audio player',r'播放器'),('an audio signal tester',r'测试仪|检测器')]
    return next((name for name,pattern in rules if re.search(pattern,text,re.I)),'audio equipment')


def score(frames,folder,product_text):
    if not ready():return {'ready':False,'reason':'本地 CLIP 模型尚未安装，仅使用画面质量与 OCR。'}
    import onnxruntime as ort
    from tokenizers import Tokenizer
    label=category(product_text)
    prompts=['a clear close-up product photograph of '+label,'a product demonstration showing '+label,
             'a company logo on a plain title card','a person talking to the camera without a visible product',
             'a website or a subscribe end screen','a blurry or completely black image']
    tokenizer=Tokenizer.from_file(str(MODEL/'tokenizer.json'))
    tokenizer.enable_truncation(max_length=77);tokenizer.enable_padding(length=77,pad_id=49407,pad_token='<|endoftext|>')
    tokens=tokenizer.encode_batch(prompts)
    ids=np.asarray([t.ids for t in tokens],dtype=np.int64);mask=np.asarray([t.attention_mask for t in tokens],dtype=np.int64)
    opts=ort.SessionOptions();opts.intra_op_num_threads=3;opts.inter_op_num_threads=1
    text_model=ort.InferenceSession(str(MODEL/'onnx/text_model_quantized.onnx'),sess_options=opts,providers=['CPUExecutionProvider'])
    feed={x.name:(mask if 'mask' in x.name else ids) for x in text_model.get_inputs()}
    names=[o.name for o in text_model.get_outputs()];target='text_embeds' if 'text_embeds' in names else names[0]
    text=text_model.run([target],feed)[0];text=text/np.maximum(np.linalg.norm(text,axis=-1,keepdims=True),1e-9)
    del text_model
    visual=ort.InferenceSession(str(MODEL/'onnx/vision_model_quantized.onnx'),sess_options=opts,providers=['CPUExecutionProvider'])
    processor=json.loads((MODEL/'preprocessor_config.json').read_text(encoding='utf-8'))
    mean=np.asarray(processor['image_mean'],dtype=np.float32);std=np.asarray(processor['image_std'],dtype=np.float32)
    names=[o.name for o in visual.get_outputs()];output='image_embeds' if 'image_embeds' in names else names[0]
    for index,f in enumerate(frames):
        if f['dark']>.96:continue
        with Image.open(folder/f['file']) as image:
            # Preserve the entire product rather than cropping the sides.
            square=ImageOps.pad(image.convert('RGB'),(224,224),method=Image.Resampling.BICUBIC,color='#20242b')
            pixels=(np.asarray(square,dtype=np.float32)/255-mean)/std
        vector=visual.run([output],{visual.get_inputs()[0].name:np.transpose(pixels,(2,0,1))[None]})[0]
        vector/=np.maximum(np.linalg.norm(vector,axis=-1,keepdims=True),1e-9)
        similarities=(vector@text.T)[0];subject=float(max(similarities[:2]));negative=float(max(similarities[2:]))
        relevance=max(0,min(100,50+(subject-negative)*300))
        f['category_similarity']=round(subject,4);f['semantic_quality']=round(relevance,1)
        f['score']=round(f['score']*.65+relevance*.35,1)
        if f['time']<22 and similarities[2]>subject+.08 and f['entropy']<2.5:f['intro']=True;f['score']=max(0,f['score']-35)
        if index%15==0:print(f'Director: {70+int(index/max(1,len(frames))*17)}%',flush=True)
    return {'ready':True,'engine':'CLIP ViT-B/32 ONNX int8','category':label,
            'note':'本地类别图文相似度，不保证具体型号、画面语义完整性或素材权属。'}
