using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace PiankeStudio {
    public sealed class Session {
        public int port, pid;
        public long started;
        public string instance;
    }
    public sealed class Controller {
        public readonly string Root, Python, Logs;
        public Session Current;
        public Action<string> Report = delegate { };
        readonly JavaScriptSerializer json = new JavaScriptSerializer();
        readonly object gate = new object();
        public string StateFile { get { return Path.Combine(Root, "data", "launcher", "session.json"); } }
        public string Url { get { return "http://127.0.0.1:" + Current.port; } }
        public Controller(string root) {
            Root = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar);
            Python = Path.Combine(Root, "tools", "python", "python.exe");
            Logs = Path.Combine(Root, "work", "launcher");
            try { Current = json.Deserialize<Session>(File.ReadAllText(StateFile)); } catch { Current = null; }
            if (Current != null && (Current.port < 18765 || Current.port > 18865 || Current.pid < 1 || Current.started < 1 || !IsGuid(Current.instance))) Current = null;
        }
        static bool IsGuid(string value) { Guid parsed; return Guid.TryParse(value, out parsed); }
        public static string MutexName(string root) {
            using (var sha = SHA256.Create()) {
                string path = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar).ToLowerInvariant();
                return "Local\\PiankeStudio-" + BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(path))).Replace("-", "");
            }
        }
        public bool OwnedProcess() {
            if (Current == null) return false;
            try {
                using (var p = Process.GetProcessById(Current.pid))
                    return !p.HasExited && p.StartTime.ToUniversalTime().Ticks == Current.started &&
                        String.Equals(p.MainModule.FileName, Python, StringComparison.OrdinalIgnoreCase);
            } catch { return false; }
        }
        public Dictionary<string, object> Get(string path) {
            if (Current == null || !path.StartsWith("/")) throw new InvalidOperationException("服务尚未启动。");
            var request = (HttpWebRequest)WebRequest.Create(Url + path);
            request.Proxy = null; request.Timeout = 1200; request.AllowAutoRedirect = false;
            using (var response = (HttpWebResponse)request.GetResponse()) {
                if (response.StatusCode != HttpStatusCode.OK) throw new IOException("本机服务响应异常。");
                using (var reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8)) {
                    char[] buffer = new char[65537]; int total = 0, count;
                    while (total < buffer.Length && (count = reader.Read(buffer, total, buffer.Length-total)) > 0) total += count;
                    if (total > 65536) throw new IOException("响应过大。");
                    return json.Deserialize<Dictionary<string, object>>(new string(buffer, 0, total));
                }
            }
        }
        public bool Healthy() {
            if (!OwnedProcess()) return false;
            try {
                var h = Get("/api/health");
                return h.ContainsKey("app") && (string)h["app"] == "product-video-editor" &&
                    h.ContainsKey("launcher_id") && (string)h["launcher_id"] == Current.instance;
            } catch { return false; }
        }
        ProcessStartInfo Command(string file, string arguments) {
            var info = new ProcessStartInfo(file, arguments) { WorkingDirectory=Root, UseShellExecute=false, CreateNoWindow=true,
                RedirectStandardOutput=true, RedirectStandardError=true, StandardOutputEncoding=Encoding.UTF8, StandardErrorEncoding=Encoding.UTF8 };
            string temp = Path.Combine(Root, "cache", "temp"); Directory.CreateDirectory(temp);
            info.EnvironmentVariables["TEMP"] = temp; info.EnvironmentVariables["TMP"] = temp;
            info.EnvironmentVariables["PYTHONUTF8"] = "1"; info.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            info.EnvironmentVariables["PIANKE_PYTHON"] = Python;
            info.EnvironmentVariables["PIANKE_NODE"] = Path.Combine(Root, "tools", "node", "node.exe");
            return info;
        }
        public int RunPython(string arguments, string label, int timeout) {
            if (!File.Exists(Python)) throw new FileNotFoundException("缺少私有 Python。请完整解压便携包，不要只复制 EXE。");
            Directory.CreateDirectory(Logs);
            string log = Path.Combine(Logs, DateTime.Now.ToString("yyyyMMdd_HHmmss_fffffff") + "-" + label + ".log");
            using (var writer = new StreamWriter(log, false, new UTF8Encoding(false)))
            using (var p = new Process()) {
                p.StartInfo = Command(Python, arguments);
                DataReceivedEventHandler record = (sender, e) => { if(e.Data != null) { lock(writer) { writer.WriteLine(e.Data); writer.Flush(); } Report(e.Data); } };
                p.OutputDataReceived += record; p.ErrorDataReceived += record;
                p.Start(); p.BeginOutputReadLine(); p.BeginErrorReadLine();
                if (!p.WaitForExit(timeout)) { KillTree(p.Id); p.WaitForExit(); throw new TimeoutException("组件操作超时。请查看启动日志后重试。"); }
                p.WaitForExit(); return p.ExitCode;
            }
        }
        static void KillTree(int pid) {
            using (var kill = Process.Start(new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "taskkill.exe"), "/PID " + pid + " /T /F") { UseShellExecute=false, CreateNoWindow=true, RedirectStandardOutput=true, RedirectStandardError=true })) {
                Task<string> output = kill.StandardOutput.ReadToEndAsync(), error = kill.StandardError.ReadToEndAsync();
                kill.WaitForExit(10000); Task.WaitAll(new Task[] { output, error }, 10000);
            }
        }
        public bool NeedsMedia() {
            foreach (string file in new [] {"tools/ffmpeg/bin/ffmpeg.exe", "tools/ffmpeg/bin/ffprobe.exe",
                "assets/fonts/Oswald/Oswald-Regular.ttf", "assets/fonts/Oswald/Oswald-SemiBold.ttf",
                "assets/fonts/SourceHanSans/SourceHanSansCN-Regular.otf", "assets/fonts/SourceHanSans/SourceHanSansCN-Bold.otf"})
                if (!File.Exists(Path.Combine(Root, file))) return true;
            return false;
        }
        public ProcessStartInfo ServerCommand(int port,string instance,string log) {
            var info=Command(Python,"-B scripts/launcher_server.py --port " + port + " --log \"" + log + "\"");
            info.EnvironmentVariables["PIANKE_LAUNCHER_ID"]=instance;
            info.RedirectStandardOutput=false; info.RedirectStandardError=false;
            info.StandardOutputEncoding=null; info.StandardErrorEncoding=null;
            return info;
        }
        public void Prepare() {
            if (OwnedProcess()) throw new InvalidOperationException("请先停止本安装目录的后台，再修复组件。");
            Report("正在从官方发布源下载并校验媒体组件；不修改商品清单。");
            if (RunPython("-B scripts/setup_core.py", "setup", 900000) != 0) throw new IOException("媒体组件准备失败。请检查网络后重试；不会跳过校验。");
            if (RunPython("-B scripts/doctor.py", "doctor", 60000) != 0) throw new IOException("环境检查未通过，请查看启动日志。");
        }
        static int FreePort() {
            for (int port=18765; port<=18865; port++) {
                var socket = new TcpListener(IPAddress.Loopback, port);
                try { socket.Start(); return port; } catch(SocketException) {} finally { socket.Stop(); }
            }
            throw new IOException("启动器端口范围已被占用。不会结束占用端口的其他程序。");
        }
        public void Start() {
            lock(gate) {
                if (Healthy()) { Report("已复用本目录的后台服务。"); return; }
                if (OwnedProcess()) throw new IOException("本目录的后台仍在启动或响应异常。请查看日志，不会重复启动或强制结束它。");
                if (NeedsMedia()) throw new IOException("请先准备媒体组件。");
                if (RunPython("-B scripts/doctor.py", "doctor", 60000) != 0) throw new IOException("环境检查失败，请点检查／修复组件。");
                Directory.CreateDirectory(Path.GetDirectoryName(StateFile)); Directory.CreateDirectory(Logs);
                Current = new Session { port=FreePort(), instance=Guid.NewGuid().ToString() };
                string log = Path.Combine(Logs, DateTime.Now.ToString("yyyyMMdd_HHmmss_fffffff") + "-server.log");
                // A launcher closing must not close stdout and crash the continuing backend.
                // Backend output is redirected by a small Python supervisor, not by GUI event readers.
                var p = new Process { StartInfo=ServerCommand(Current.port,Current.instance,log) };
                p.Start(); Current.pid=p.Id; Current.started=p.StartTime.ToUniversalTime().Ticks;
                string temp=StateFile+".tmp"; File.WriteAllText(temp, json.Serialize(Current), new UTF8Encoding(false));
                if (File.Exists(StateFile)) File.Replace(temp, StateFile, null); else File.Move(temp, StateFile);
                Report("正在启动本机服务…");
                for (int n=0;n<100;n++) {
                    if (Healthy()) { p.Dispose(); Report("工作台已就绪。"); return; }
                    if (p.HasExited) { p.Dispose(); throw new IOException("后台启动失败，请查看启动日志。现有源代码工作台若正在运行，请先退出它。"); }
                    Thread.Sleep(200);
                }
                p.Dispose(); throw new TimeoutException("后台暂未就绪，保留进程供诊断；不会再启动第二份。");
            }
        }
        public void Stop() {
            lock(gate) {
                if (!OwnedProcess()) { Report("本目录没有可管理的后台；未结束任何其他程序。"); return; }
                if (!Healthy()) throw new IOException("无法验证后台身份或状态，拒绝强制结束。请查看日志。");
                var health=Get("/api/health");
                if (health.ContainsKey("busy") && Convert.ToBoolean(health["busy"])) throw new IOException("有视频正在处理，请等本条完成后再停止。");
                int pid=Current.pid;
                if (!OwnedProcess() || !Healthy()) throw new IOException("后台状态已改变，停止操作取消。");
                var request=(HttpWebRequest)WebRequest.Create(Url+"/api/launcher/stop");
                request.Proxy=null; request.Method="POST"; request.Timeout=4000; request.AllowAutoRedirect=false;
                request.ContentType="application/json"; request.Headers["Origin"]=Url;
                byte[] body=Encoding.UTF8.GetBytes(json.Serialize(new {instance=Current.instance}));
                request.ContentLength=body.Length;
                using(var stream=request.GetRequestStream())stream.Write(body,0,body.Length);
                using(var response=request.GetResponse()) {}
                for(int n=0;n<100 && OwnedProcess();n++) Thread.Sleep(100);
                if (OwnedProcess()) throw new IOException("后台尚未退出，请稍后重试。");
                Report("后台已停止。商品清单和视频均保留。");
            }
        }
        public void Open() { if (!Healthy()) throw new IOException("工作台尚未就绪。"); Shell(Url); }
        public static void Shell(string target) { Process.Start(new ProcessStartInfo(target) { UseShellExecute=true }); }
    }

    public sealed class LauncherForm : Form {
        readonly Controller controller;
        readonly Label status=new Label(), detail=new Label();
        readonly ProgressBar progress=new ProgressBar();
        readonly List<Button> actions=new List<Button>();
        bool working;
        public LauncherForm(Controller c, bool autoStart=true) {
            controller=c; Text="片刻Studio · 启动器"; ClientSize=new Size(720,420); MinimumSize=new Size(736,459);
            StartPosition=FormStartPosition.CenterScreen; BackColor=Color.FromArgb(246,248,252);
            Font=new Font("Microsoft YaHei UI",10); AutoScaleMode=AutoScaleMode.Dpi;
            var title=new Label { Text="片刻Studio", Font=new Font(Font.FontFamily,28,FontStyle.Bold), Location=new Point(32,30), Size=new Size(640,55) }; Controls.Add(title);
            Controls.Add(new Label { Text="让商品视频制作，从一次双击开始。", ForeColor=Color.DimGray, Location=new Point(35,92), Size=new Size(650,28) });
            status.Text="正在检查本机环境…"; status.Font=new Font(Font.FontFamily,12,FontStyle.Bold); status.Location=new Point(35,150); status.Size=new Size(650,32); Controls.Add(status);
            detail.Text="只在本机运行 · 不需要系统 Python · 无自动发布"; detail.Location=new Point(35,194); detail.Size=new Size(650,58); Controls.Add(detail);
            progress.Location=new Point(35,260); progress.Size=new Size(650,5); progress.Style=ProgressBarStyle.Marquee; progress.Visible=false; Controls.Add(progress);
            AddButton("打开工作台",35,294,true, async ()=>await StartAndOpen());
            AddButton("停止后台",205,294,false,async ()=>await Work(()=>controller.Stop()));
            AddButton("检查／修复",375,294,false,async ()=> {
                if(MessageBox.Show(this,"将检查本机环境，并从官方源下载缺失的 FFmpeg 和字体，约需数百 MB。不会清空配置和视频。是否继续？","准备组件",MessageBoxButtons.YesNo,MessageBoxIcon.Information)==DialogResult.Yes)
                    await Work(()=>controller.Prepare());
            });
            AddButton("查看日志",545,294,false,async ()=>{ Directory.CreateDirectory(controller.Logs); Controller.Shell(controller.Logs); await Task.CompletedTask; });
            Controls.Add(new Label { Text="关闭这个窗口后，后台继续运行；需要退出服务时请先点「停止后台」。", Location=new Point(35,364), Size=new Size(650,36), ForeColor=Color.DimGray });
            controller.Report = text => { if(!IsDisposed && IsHandleCreated) BeginInvoke((Action)(()=>detail.Text=text.Length>180?text.Substring(0,180)+"…":text)); };
            if(autoStart)Shown += async (sender,e)=>await StartAndOpen();
            FormClosing += (sender,e)=> { if(working) { e.Cancel=true; MessageBox.Show(this,"组件准备或启动操作尚未完成，请稍后关闭。"); } };
        }
        void AddButton(string text,int x,int y,bool primary,Func<Task> action) {
            var b=new Button { Text=text, Location=new Point(x,y), Size=new Size(145,46), FlatStyle=FlatStyle.Flat,
                BackColor=primary?Color.FromArgb(43,94,240):Color.White, ForeColor=primary?Color.White:Color.FromArgb(40,48,65) };
            b.FlatAppearance.BorderSize=0; b.Click += async (sender,e)=>await action(); Controls.Add(b); actions.Add(b);
        }
        async Task StartAndOpen() {
            bool prepare=controller.NeedsMedia();
            if(prepare && MessageBox.Show(this,"首次使用需要联网下载并校验 FFmpeg 和字体（约 100–230 MB）。运行环境已随包携带，无需安装系统 Python。是否现在准备？","首次启动",MessageBoxButtons.YesNo,MessageBoxIcon.Information)!=DialogResult.Yes) { status.Text="尚未准备媒体组件"; detail.Text="随时可点击「打开工作台」重试。"; return; }
            await Work(()=>{ if(prepare) controller.Prepare(); controller.Start(); controller.Open(); });
        }
        async Task Work(Action action) {
            if(working)return; working=true; progress.Visible=true; foreach(var b in actions)b.Enabled=false;
            status.Text="正在处理，请稍候…";
            try { await Task.Run(action); status.Text=controller.Healthy()?"工作台正在本机运行":"操作完成"; }
            catch(Exception e) { status.Text="需要处理"; detail.Text=e.Message; MessageBox.Show(this,e.Message,"片刻Studio",MessageBoxButtons.OK,MessageBoxIcon.Warning); }
            finally { working=false; progress.Visible=false; foreach(var b in actions)b.Enabled=true; }
        }
    }
    static class Program {
        [STAThread] static int Main(string[] args) {
            string root=AppDomain.CurrentDomain.BaseDirectory;
            var controller=new Controller(root);
            bool smoke=args.Length==1 && args[0]=="--smoke-test";
            bool prepare=args.Length==1 && args[0]=="--prepare";
            bool core=args.Length==1 && args[0]=="--self-test-core";
            bool preview=args.Length==1 && args[0]=="--render-preview";
            if(args.Length==1 && args[0]=="--can-update") {
                if(controller.OwnedProcess())return 1;
                string serverLock=Path.Combine(root,"data","editor","server.lock");
                if(File.Exists(serverLock)) {
                    try { using(var file=new FileStream(serverLock,FileMode.Open,FileAccess.ReadWrite,FileShare.ReadWrite)) { file.Lock(0,1); file.Unlock(0,1); } }
                    catch { return 1; }
                }
                try { using(var existing=Mutex.OpenExisting(Controller.MutexName(root)))return 1; }
                catch(WaitHandleCannotBeOpenedException) { return 0; }
            }
            bool created;
            using(var mutex=new Mutex(true,Controller.MutexName(root),out created)) {
                if(!created) {
                    if(controller.Healthy()) { if(!smoke)controller.Open(); return 0; }
                    if(!smoke)MessageBox.Show("启动器正在处理，请返回已打开的片刻Studio窗口。");
                    return 1;
                }
                try {
                    if(core) {
                        if(controller.Current!=null || controller.OwnedProcess() || controller.Healthy())throw new Exception("Unexpected process ownership");
                        var command=controller.ServerCommand(18765,Guid.NewGuid().ToString(),"test-only.log");
                        if(command.RedirectStandardOutput || command.RedirectStandardError || command.StandardOutputEncoding!=null || command.StandardErrorEncoding!=null || !command.CreateNoWindow)
                            throw new Exception("Background process stdio contract violated");
                        controller.Stop(); return 0;
                    }
                    if(prepare) { controller.Prepare(); return 0; }
                    if(smoke) {
                        if(controller.OwnedProcess())throw new Exception("Smoke requires an isolated fresh directory");
                        controller.Start(); int pid=controller.Current.pid; controller.Start();
                        if(controller.Current.pid!=pid || !controller.Healthy())throw new Exception("Duplicate/reuse check failed");
                        var state=controller.Current;
                        controller.Current=new Session { pid=Process.GetCurrentProcess().Id, port=state.port, started=1, instance=state.instance };
                        controller.Stop(); // Must not kill this unrelated process.
                        controller.Current=state;
                        if(!controller.Healthy())throw new Exception("Ownership guard failed");
                        if(!controller.Get("/api/snapany").ContainsKey("mode"))throw new Exception("Fresh API check failed");
                        controller.Stop();
                        if(controller.OwnedProcess())throw new Exception("Stop failed");
                        Directory.CreateDirectory(controller.Logs);
                        File.WriteAllText(Path.Combine(controller.Logs,"smoke-proof.json"),"{\"ok\":true,\"startup\":true,\"reuse\":true,\"ownership_guard\":true,\"stop\":true}");
                        return 0;
                    }
                    if(args.Length>0 && !preview)throw new ArgumentException("Unsupported launcher argument");
                    Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
                    using(var form=new LauncherForm(controller,!preview)) {
                        if(preview) {
                            Directory.CreateDirectory(controller.Logs);
                            form.ShowInTaskbar=false; form.StartPosition=FormStartPosition.Manual;
                            form.Location=new Point(-20000,-20000); form.Show(); Application.DoEvents();
                            using(var bitmap=new Bitmap(form.Width,form.Height)) {
                                form.DrawToBitmap(bitmap,new Rectangle(0,0,form.Width,form.Height));
                                bitmap.Save(Path.Combine(controller.Logs,"launcher-preview.png"),System.Drawing.Imaging.ImageFormat.Png);
                            }
                        } else Application.Run(form);
                    }
                    return 0;
                } catch(Exception e) {
                    try { Directory.CreateDirectory(controller.Logs); File.WriteAllText(Path.Combine(controller.Logs,"launcher-error.log"),e.ToString()); } catch { }
                    if(!smoke && !prepare && !core)MessageBox.Show(e.Message,"片刻Studio启动失败");
                    return 1;
                } finally { mutex.ReleaseMutex(); }
            }
        }
    }
}
