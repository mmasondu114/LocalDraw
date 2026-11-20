using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

internal static class SetupLauncher
{
    [STAThread]
    private static int Main()
    {
        // Keep the wizard small. The CUDA archive exceeds its 32-bit file limit.
        string temp = Path.Combine(Path.GetTempPath(), "LocalDrawSetup-" + Guid.NewGuid().ToString("N"));
        string wizard = Path.Combine(temp, "Setup.exe");
        try
        {
            Directory.CreateDirectory(temp);
            using (Stream source = Assembly.GetExecutingAssembly().GetManifestResourceStream("wizard"))
            using (FileStream output = new FileStream(wizard, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                source.CopyTo(output);
            string command = Environment.CommandLine.TrimStart();
            int end = command.StartsWith("\"") ? command.IndexOf('"', 1) + 1 : command.IndexOf(' ');
            var start = new ProcessStartInfo(wizard, end < 0 ? "" : command.Substring(end).TrimStart());
            start.UseShellExecute = false;
            start.EnvironmentVariables["LOCALDRAW_INSTALL_SOURCE"] = Application.ExecutablePath;
            using (Process process = Process.Start(start))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "LocalDraw 安装程序", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        finally
        {
            try { File.Delete(wizard); Directory.Delete(temp); } catch (IOException) { }
        }
    }
}
