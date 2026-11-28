using System;
using System.Collections.Generic;
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
    private static readonly string Root = AppDomain.CurrentDomain.BaseDirectory;
    private const string Title = "本地漫画工作室 · LocalDraw";
    private const string AppUserModelId = "LocalDraw.Studio.Desktop.2";

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)] private static extern int SetCurrentProcessExplicitAppUserModelID(string appId);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc proc, IntPtr lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int maxCount);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hWnd);
    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    private static bool ActivateExistingWindow()
    {
        IntPtr found = IntPtr.Zero;
        EnumWindows((hWnd, lParam) =>
        {
            if (!IsWindowVisible(hWnd)) return true;
            var sb = new StringBuilder(256);
            GetWindowText(hWnd, sb, sb.Capacity);
            string title = sb.ToString();
            if (title.Contains("LocalDraw") || title.Contains("本地漫画工作室"))
            {
                found = hWnd;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        if (found == IntPtr.Zero) return false;
        if (IsIconic(found)) ShowWindow(found, 9);
        SetForegroundWindow(found);
        return true;
    }

    [STAThread]
    private static int Main(string[] args)
    {
        try { SetCurrentProcessExplicitAppUserModelID(AppUserModelId); } catch { }
        bool smoke = args.Length == 1 && args[0] == "--self-test";
        try
        {
            if (args.Length > 0 && !smoke)
                throw new ArgumentException("不支持的启动参数。");

            string key;
            using (SHA256 hash = SHA256.Create())
                key = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(Root.ToUpperInvariant()))).Replace("-", "");

            if (!smoke && ActivateExistingWindow())
                return 0;

            var installation = new JavaScriptSerializer().Deserialize<Dictionary<string, string>>(
                File.ReadAllText(Path.Combine(Root, @"config\installed-runtime.json"), Encoding.UTF8));
            string pythonw = Path.Combine(Root, installation["pythonw"]);
            string entry = Path.Combine(Root, "launch.py");
            if (!File.Exists(pythonw) || !File.Exists(entry))
                throw new FileNotFoundException("请将 LocalDraw.exe 保留在完整工作室目录内，缺少 Python 环境或 launch.py。");

            string arguments = "\"" + entry + "\"" + (smoke ? " --self-test" : "");
            var psi = new ProcessStartInfo(pythonw, arguments)
            {
                WorkingDirectory = Root,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["COMIC_STUDIO_ROOT"] = Root;
            psi.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";

            using (Process proc = Process.Start(psi))
            {
                if (proc == null) throw new InvalidOperationException("无法启动 LocalDraw 原生程序。");
                if (smoke)
                {
                    if (!proc.WaitForExit(30000))
                    {
                        proc.Kill();
                        throw new TimeoutException("原生界面自检超时。");
                    }
                    return proc.ExitCode;
                }
            }
            return 0;
        }
        catch (Exception error)
        {
            if (!smoke)
                MessageBox.Show(error.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
