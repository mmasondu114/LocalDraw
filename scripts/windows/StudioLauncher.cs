using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.8", FrameworkDisplayName = ".NET Framework 4.8")]

internal static class StudioLauncher
{
    internal static readonly string Root = AppDomain.CurrentDomain.BaseDirectory;
    internal const string Title = "本地漫画工作室 · Local Draw";
    internal static string DataRoot { get { return Environment.GetEnvironmentVariable("COMIC_STUDIO_ROOT") ?? Root; } }
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr FindWindow(string cls, string title);
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
                    IntPtr existing = FindWindow(null, Title);
                    if (existing != IntPtr.Zero) { if (IsIconic(existing)) ShowWindow(existing, 9); SetForegroundWindow(existing); }
                    return 0;
                }
                try
                {
                    return RunDesktop(smoke);
                }
                finally { instance.ReleaseMutex(); }
            }
        }
        catch (Exception error)
        {
            if (!quiet && !smoke) MessageBox.Show(error.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

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
        string python = Path.Combine(Root, @".venv\Scripts\pythonw.exe");
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
        return ServiceAddress();
    }

    internal static string ServiceAddress()
    {
        string configRoot = DataRoot;
        var config = new JavaScriptSerializer().DeserializeObject(File.ReadAllText(Path.Combine(configRoot, @"config\settings.json"), Encoding.UTF8)) as System.Collections.Generic.Dictionary<string, object>;
        int port = Convert.ToInt32(config["port"]);
        if (port < 1 || port > 65535) throw new InvalidDataException("工作室端口设置无效。");
        return "http://127.0.0.1:" + port;
    }
}

