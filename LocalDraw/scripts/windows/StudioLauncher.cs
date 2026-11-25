using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

[assembly: System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.8", FrameworkDisplayName = ".NET Framework 4.8")]

internal static class StudioLauncher
{
    internal static readonly string Root = AppDomain.CurrentDomain.BaseDirectory;
    internal const string Title = "本地漫画工作室 · Local Draw";
    internal static string SdkPath { get { return Path.Combine(Root, @"runtime\desktop"); } }
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] private static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] internal static extern IntPtr GetWindowDpiAwarenessContext(IntPtr window);
    [DllImport("user32.dll")] internal static extern bool AreDpiAwarenessContextsEqual(IntPtr first, IntPtr second);
    [DllImport("user32.dll")] internal static extern uint GetDpiForWindow(IntPtr window);

    [STAThread]
    private static int Main(string[] args)
    {
        bool quiet = args.Length == 1 && args[0] == "--no-browser";
        bool smoke = args.Length == 1 && args[0] == "--self-test";
        try
        {
            if (args.Length > 0 && !quiet && !smoke) throw new ArgumentException("不支持的启动参数。");
            if (quiet) { StartServer(); return 0; }
            string key;
            using (SHA256 hash = SHA256.Create())
                key = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(Root.ToUpperInvariant()))).Replace("-", "");
            bool first;
            using (Mutex instance = new Mutex(true, @"Local\LocalDraw-Desktop-" + key + (smoke ? "-test" : ""), out first))
            {
                if (!first)
                {
                    string currentExe = Application.ExecutablePath;
                    foreach (Process candidate in Process.GetProcessesByName(Path.GetFileNameWithoutExtension(currentExe)))
                    using (candidate)
                    {
                        try
                        {
                            if (candidate.MainWindowHandle == IntPtr.Zero || !String.Equals(candidate.MainModule.FileName, currentExe, StringComparison.OrdinalIgnoreCase)) continue;
                            IntPtr existing = candidate.MainWindowHandle;
                            if (IsIconic(existing)) ShowWindow(existing, 9);
                            SetForegroundWindow(existing);
                            break;
                        }
                        catch (System.ComponentModel.Win32Exception) { }
                        catch (InvalidOperationException) { }
                    }
                    return 0;
                }
                try
                {
                    PrepareSdk();
                    AppDomain.CurrentDomain.AssemblyResolve += (sender, e) => {
                        string name = new AssemblyName(e.Name).Name;
                        if (name != "Microsoft.Web.WebView2.Core" && name != "Microsoft.Web.WebView2.WinForms") return null;
                        return Assembly.LoadFrom(Path.Combine(SdkPath, name + ".dll"));
                    };
                    return RunDesktop(smoke);
                }
                finally { instance.ReleaseMutex(); }
            }
        }
        catch (Exception error)
        {
            if (!quiet) MessageBox.Show(error.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    private static void PrepareSdk()
    {
        Directory.CreateDirectory(SdkPath);
        using (var zip = new ZipArchive(Assembly.GetExecutingAssembly().GetManifestResourceStream("desktop.zip")))
        foreach (string name in new[] { "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "WebView2Loader.dll" })
        {
            string target = Path.Combine(SdkPath, name);
            using (Stream stream = zip.GetEntry(name).Open())
            using (MemoryStream buffer = new MemoryStream())
            {
                if (stream == null) throw new InvalidOperationException("安装文件缺失：" + name);
                stream.CopyTo(buffer);
                byte[] embedded = buffer.ToArray();
                using (SHA256 hash = SHA256.Create())
                {
                    if (File.Exists(target) && Convert.ToBase64String(hash.ComputeHash(File.ReadAllBytes(target))) == Convert.ToBase64String(hash.ComputeHash(embedded))) continue;
                }
                File.WriteAllBytes(target, embedded);
            }
        }
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static int RunDesktop(bool smoke)
    {
        // Set before creating any HWND; Windows must not bitmap-scale the UI.
        SetProcessDpiAwarenessContext(new IntPtr(-4)); // PER_MONITOR_AWARE_V2
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        using (StudioWindow window = new StudioWindow(smoke))
        {
            Application.Run(window);
            return window.Result;
        }
    }

    internal static string StartServer()
    {
        var installation = new JavaScriptSerializer().Deserialize<System.Collections.Generic.Dictionary<string, string>>(
            File.ReadAllText(Path.Combine(Root, @"config\installed-runtime.json"), Encoding.UTF8));
        string python = Path.Combine(Root, installation["pythonw"]);
        string entry = Path.Combine(Root, "start_studio.py");
        if (!File.Exists(python) || !File.Exists(entry))
            throw new FileNotFoundException("请将 LocalDraw.exe 保留在完整工作室目录内，缺少 Python 环境或 start_studio.py。");
        string logs = Path.Combine(Root, @"data\logs");
        Directory.CreateDirectory(logs);
        string logPath = Path.Combine(logs, "desktop-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + "-" + Process.GetCurrentProcess().Id + ".log");
        using (StreamWriter log = new StreamWriter(logPath, false, new UTF8Encoding(false)))
        using (Process process = new Process())
        {
            log.AutoFlush = true;
            process.StartInfo = new ProcessStartInfo(python, "\"" + entry + "\" --no-browser") {
                WorkingDirectory = Root, UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            process.StartInfo.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            process.StartInfo.EnvironmentVariables["COMIC_STUDIO_ROOT"] = Root;
            process.StartInfo.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
            process.OutputDataReceived += (s, e) => { if (e.Data != null) lock (log) log.WriteLine(e.Data); };
            process.ErrorDataReceived += (s, e) => { if (e.Data != null) lock (log) log.WriteLine(e.Data); };
            process.Start(); process.BeginOutputReadLine(); process.BeginErrorReadLine();
            if (!process.WaitForExit(45000))
            {
                process.Kill(); process.WaitForExit();
                throw new TimeoutException("后台启动超时。请重试或查看日志：\n" + logPath);
            }
            process.WaitForExit(); // Drain async callbacks before disposing the log writer.
            if (process.ExitCode != 0) throw new Exception("后台尚未就绪。请重试或查看日志：\n" + logPath);
        }
        string configRoot = Root;
        var config = new JavaScriptSerializer().DeserializeObject(File.ReadAllText(Path.Combine(configRoot, @"config\settings.json"), Encoding.UTF8)) as System.Collections.Generic.Dictionary<string, object>;
        int port = Convert.ToInt32(config["port"]);
        if (port < 1 || port > 65535) throw new InvalidDataException("工作室端口设置无效。");
        return "http://127.0.0.1:" + port;
    }
}

internal sealed class StudioWindow : Form
{
    private readonly bool smoke;
    private readonly WebView2 view = new WebView2();
    private readonly Label status = new Label();
    private readonly Button retry = new Button();
    private readonly Panel loading = new Panel();
    private Uri origin;
    internal int Result { get; private set; }

    internal StudioWindow(bool smokeTest)
    {
        smoke = smokeTest;
        Text = StudioLauncher.Title; StartPosition = FormStartPosition.CenterScreen;
        AutoScaleDimensions = new SizeF(96F, 96F);
        AutoScaleMode = AutoScaleMode.Dpi;
        ClientSize = new Size(1440, 900); MinimumSize = new Size(960, 640);
        if (!smoke) WindowState = FormWindowState.Maximized;
        BackColor = Color.FromArgb(244, 246, 242);
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        view.Dock = DockStyle.Fill; view.DefaultBackgroundColor = BackColor;
        view.ZoomFactor = 1.0;
        loading.Dock = DockStyle.Fill; loading.BackColor = BackColor;
        status.AutoSize = false; status.Dock = DockStyle.Fill; status.TextAlign = ContentAlignment.MiddleCenter;
        status.Font = new Font("Microsoft YaHei UI", 15); status.ForeColor = Color.FromArgb(36, 100, 85);
        status.Text = "本地漫画工作室\n\n正在准备你的创作空间…";
        retry.Text = "重新连接"; retry.Dock = DockStyle.Bottom; retry.Height = 48; retry.Visible = false;
        retry.Click += async (s, e) => await Connect();
        loading.Controls.Add(status); loading.Controls.Add(retry);
        Controls.Add(view); Controls.Add(loading);
        Shown += async (s, e) => await Connect();
    }

    private bool IsLocal(string address)
    {
        Uri target;
        return origin != null && Uri.TryCreate(address, UriKind.Absolute, out target) && target.Scheme == origin.Scheme && target.Authority == origin.Authority;
    }

    private async Task Connect()
    {
        retry.Visible = false; loading.Visible = true; loading.BringToFront();
        status.Text = "本地漫画工作室\n\n正在准备你的创作空间…";
        try
        {
            Task<string> serverTask = smoke ? null : Task.Run(() => StudioLauncher.StartServer());
            if (view.CoreWebView2 == null)
            {
                CoreWebView2Environment.SetLoaderDllFolderPath(StudioLauncher.SdkPath);
                var environment = await CoreWebView2Environment.CreateAsync(null, Path.Combine(StudioLauncher.Root, @"data\desktop-profile"));
                if (IsDisposed) return;
                await view.EnsureCoreWebView2Async(environment);
                view.CoreWebView2.Settings.AreDevToolsEnabled = false;
                view.CoreWebView2.Settings.IsStatusBarEnabled = false;
                view.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
                view.CoreWebView2.ContextMenuRequested += (s, e) => {
                    if (!e.ContextMenuTarget.IsEditable) e.Handled = true;
                };
                view.CoreWebView2.NavigationStarting += (s, e) => {
                    if (smoke) return;
                    if (!IsLocal(e.Uri)) e.Cancel = true;
                };
                view.CoreWebView2.NewWindowRequested += (s, e) => {
                    e.Handled = true;
                    if (IsLocal(e.Uri)) OpenPreview(new Uri(e.Uri));
                };
                view.CoreWebView2.NavigationCompleted += async (s, e) => {
                    if (e.WebErrorStatus == CoreWebView2WebErrorStatus.OperationCanceled) return;
                    if (!e.IsSuccess) { ShowError("界面加载失败（" + e.WebErrorStatus + "），请检查后台后重新连接。"); return; }
                    Result = 0; loading.Visible = false;
                    if (smoke)
                    {
                        string title = await view.CoreWebView2.ExecuteScriptAsync("document.title");
                        bool perMonitorV2 = StudioLauncher.AreDpiAwarenessContextsEqual(StudioLauncher.GetWindowDpiAwarenessContext(Handle), new IntPtr(-4));
                        if (!perMonitorV2) { ShowError("DPI self-test failed: window is not PerMonitorV2."); return; }
                        string pixelRatio = await view.CoreWebView2.ExecuteScriptAsync("devicePixelRatio");
                        Directory.CreateDirectory(Path.Combine(StudioLauncher.Root, @"data\logs"));
                        File.WriteAllText(Path.Combine(StudioLauncher.Root, @"data\logs\desktop-smoke.json"), "{\"title\":" + title + ",\"native_window\":true,\"per_monitor_v2\":true,\"window_dpi\":" + StudioLauncher.GetDpiForWindow(Handle) + ",\"device_pixel_ratio\":" + pixelRatio + "}");
                        string previousError = Path.Combine(StudioLauncher.Root, @"data\logs\desktop-smoke-error.txt");
                        if (File.Exists(previousError)) File.Delete(previousError);
                        Close();
                    }
                };
            }
            if (smoke) { view.NavigateToString("<!doctype html><title>LocalDraw desktop test</title><h1>Local Draw</h1>"); return; }
            string url = await serverTask;
            if (IsDisposed) return;
            origin = new Uri(url);
            view.CoreWebView2.Navigate(url);
        }
        catch (Exception error) { if (!IsDisposed) ShowError(error.Message); }
    }

    private void ShowError(string message)
    {
        Result = 1; loading.Visible = true; loading.BringToFront();
        status.Text = "暂时无法打开工作室\n\n" + message; retry.Visible = true;
        if (smoke)
        {
            Directory.CreateDirectory(Path.Combine(StudioLauncher.Root, @"data\logs"));
            File.WriteAllText(Path.Combine(StudioLauncher.Root, @"data\logs\desktop-smoke-error.txt"), message);
            Close();
        }
    }

    private async void OpenPreview(Uri target)
    {
        Form preview = new Form { Text = "作品预览 · Local Draw", Width = 1100, Height = 800, StartPosition = FormStartPosition.CenterParent };
        WebView2 pane = new WebView2 { Dock = DockStyle.Fill };
        preview.Controls.Add(pane); preview.Show(this);
        try
        {
            await pane.EnsureCoreWebView2Async(view.CoreWebView2.Environment);
            if (preview.IsDisposed) return;
            pane.CoreWebView2.Settings.AreDevToolsEnabled = false;
            pane.CoreWebView2.NewWindowRequested += (s, e) => e.Handled = true;
            pane.CoreWebView2.NavigationStarting += (s, e) => { if (!IsLocal(e.Uri)) e.Cancel = true; };
            pane.Source = target;
        }
        catch (Exception error) { if (!preview.IsDisposed) preview.Close(); if (!IsDisposed) MessageBox.Show(this, error.Message, "预览失败"); }
    }
}
