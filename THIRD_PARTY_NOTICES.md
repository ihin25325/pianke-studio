# Third-party components

The project source is MIT. This does **not** relicense external executables,
fonts, models, downloaded videos, logos or trademarks. These components are
not bundled as binaries/model weights in the source release.

| Component | Upstream / license information | How obtained |
| --- | --- | --- |
| Python | https://docs.python.org/3/license.html | User installs 64-bit Python |
| Pillow | https://github.com/python-pillow/Pillow/blob/main/LICENSE | Core pip installation |
| yt-dlp | https://github.com/yt-dlp/yt-dlp#license | Core pip installation; inspect upstream notices for dependencies and packaged distributions |
| FFmpeg | https://ffmpeg.org/legal.html | Setup downloads BtbN GPL builds (Gyan essentials fallback); publisher SHA256 checked. Preserve the actual downloaded license/source information when redistributing binaries |
| BtbN builds | https://github.com/BtbN/FFmpeg-Builds | Primary external download, GPL build, not bundled |
| Windows FFmpeg builds | https://www.gyan.dev/ffmpeg/builds/ | External download, not bundled |
| Node.js | https://github.com/nodejs/node/blob/main/LICENSE | User installation, used by YouTube extractor |
| Oswald / Source Han Sans | https://github.com/googlefonts/OswaldFont and https://github.com/adobe-fonts/source-han-sans | Default font download; preserve each SIL OFL 1.1 notice |
| Additional fonts | https://github.com/google/fonts and upstream URLs in font download scripts | Optional font pack, SIL OFL 1.1; generated weights keep notices |
| faster-whisper / Whisper model | https://github.com/SYSTRAN/faster-whisper and https://github.com/openai/whisper | Optional speech setup, upstream MIT notices |
| OpenCV / ONNX Runtime / RapidOCR / CLIP | https://github.com/opencv/opencv, https://github.com/microsoft/onnxruntime, https://github.com/RapidAI/RapidOCR, https://github.com/openai/CLIP | Optional local-vision setup; retain upstream component/model notices |
| Qwen3 / llama.cpp | https://huggingface.co/Qwen and https://github.com/ggml-org/llama.cpp | Optional translation setup; consult exact downloaded model/build licenses |
| resvg-py | https://pypi.org/project/resvg-py/ | Optional SVG rasterization installation |

DeepSeek and SnapAny are external hosted services, not MIT components. Each
requires the user's own account/key and is subject to its own terms and fees.
Neither is required to start the local app or run the synthetic demo.

Downloading a public URL does not grant permission to reuse its content. Only
process media you own or are authorized to use. No right to a third-party logo
or trademark is conveyed by this repository. The included demo generator uses
FFmpeg synthetic patterns and a generated tone, not third-party product footage.
