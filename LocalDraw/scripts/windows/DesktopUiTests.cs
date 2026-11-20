// Offline UI acceptance. Uses the real frontend and isolated fetch fixtures.
using System;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

[assembly: System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.8", FrameworkDisplayName = ".NET Framework 4.8")]

internal static class DesktopUiTests
{
    static string root;
    static string output;
    static int result = 1;
    [STAThread] static int Main(string[] args)
    {
        root = args[0]; output = Path.Combine(root, @"data\test-results\desktop-redesign");
        Directory.CreateDirectory(output);
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) => {
            string path = Path.Combine(root, @"runtime\desktop", new AssemblyName(e.Name).Name + ".dll");
            return File.Exists(path) ? Assembly.LoadFrom(path) : null;
        };
        Run(); return result;
    }
    [MethodImpl(MethodImplOptions.NoInlining)] static void Run()
    {
        Application.EnableVisualStyles();
        using (Form form = new Form { ClientSize = new Size(1440, 900), Text = "Local Draw UI acceptance" })
        using (WebView2 view = new WebView2 { Dock = DockStyle.Fill })
        {
            form.Controls.Add(view);
            form.Shown += async (s, e) => {
                Exception failure = null;
                try
                {
                    CoreWebView2Environment.SetLoaderDllFolderPath(Path.Combine(root, @"runtime\desktop"));
                    var env = await CoreWebView2Environment.CreateAsync(null, Path.Combine(output, "profile"));
                    await view.EnsureCoreWebView2Async(env);
                    string html = File.ReadAllText(Path.Combine(root, @"web\index.html"));
                    html = html.Replace("<link rel=\"stylesheet\" href=\"/style.css\" />", "<style>" + File.ReadAllText(Path.Combine(root, @"web\style.css")) + "</style>");
                    html = html.Replace("<head>", "<head><script>" + File.ReadAllText(Path.Combine(root, @"scripts\desktop-ui-fixture.js")) + "</script>");
                    html = Regex.Replace(html, "<script defer src=\"/([^\"]+)\"></script>", m => "<script>" + File.ReadAllText(Path.Combine(root, "web", m.Groups[1].Value)).Replace("</script", "<\\/script") + "</script>");
                    view.NavigateToString(html);
                    await Wait(view, "document.querySelectorAll('.home-choice').length===3");
                    await Check(view, "getComputedStyle(document.querySelector('.shell')).display==='none'", "Editor flashes on home");
                    await Capture(view, "home-desktop.png");
                    form.ClientSize = new Size(375, 812);
                    await Task.Delay(200);
                    await Check(view, "document.documentElement.scrollWidth<=innerWidth+1", "Home overflows at 375px");
                    await Capture(view, "home-mobile.png");
                    form.ClientSize = new Size(1440, 900);
                    await view.ExecuteScriptAsync("document.querySelector('.home-choice button').click()");
                    await Wait(view, "!!document.querySelector('[data-edit-key=prompt-positive]')");
                    await Check(view, "!document.body.classList.contains('at-home')", "Prompt stays hidden");
                    await view.ExecuteScriptAsync("let p=document.querySelector('[data-edit-key=prompt-positive]');p.value='A quiet garden at sunset';p.dispatchEvent(new Event('input',{bubbles:true}))");
                    await Capture(view, "prompt-desktop.png");
                    await Check(view, "document.documentElement.scrollWidth<=innerWidth+1", "Prompt overflows horizontally");
                    form.MaximumSize = new Size(5000, 3000);
                    form.ClientSize = new Size(3440, 1440);
                    // Test exact content pixels independently of Windows' caption
                    // and desktop track-size limits on the top-level test window.
                    view.Dock = DockStyle.None;
                    view.Bounds = new Rectangle(0, 0, 3440, 1440);
                    await Task.Delay(250);
                    File.WriteAllText(Path.Combine(output, "ultrawide-dpi.json"), await view.ExecuteScriptAsync("({width:innerWidth,height:innerHeight,devicePixelRatio,physicalWidth:Math.round(innerWidth*devicePixelRatio),physicalHeight:Math.round(innerHeight*devicePixelRatio)})"));
                    File.WriteAllText(Path.Combine(output, "ultrawide-host.txt"), "Form=" + form.ClientSize + "; View=" + view.Size + "; Screen=" + Screen.FromControl(form).Bounds);
                    await Check(view, "Math.abs(innerWidth*devicePixelRatio-3440)<=2 && Math.abs(innerHeight*devicePixelRatio-1440)<=2", "Ultrawide viewport is not rendered at 3440x1440 physical pixels");
                    await Check(view, "document.documentElement.scrollWidth<=innerWidth+1", "Ultrawide workspace overflows horizontally");
                    await Check(view, "Math.abs(document.querySelector('.prompt-gallery').getBoundingClientRect().top-document.querySelector('.prompt-input').getBoundingClientRect().top)<2 && document.querySelector('.prompt-gallery').getBoundingClientRect().bottom<=innerHeight+1", "Ultrawide preview wraps below the editing panels");
                    await Capture(view, "prompt-3440x1440.png");
                    view.Dock = DockStyle.Fill;
                    form.ClientSize = new Size(1440, 900);
                    await view.ExecuteScriptAsync("document.querySelector('#home-entry').click()");
                    await Wait(view, "document.body.classList.contains('at-home')");
                    await view.ExecuteScriptAsync("document.querySelector('.home-choice button').click()");
                    await Wait(view, "!document.body.classList.contains('at-home')");
                    await Check(view, "document.querySelector('[data-edit-key=prompt-positive]').value==='A quiet garden at sunset'", "Prompt draft was lost");
                    await view.ExecuteScriptAsync("document.querySelector('#queue-entry').click()");
                    await Wait(view, "!!document.querySelector('#studio-queue-dialog[open]')");
                    await Capture(view, "queue-desktop.png");
                    await view.ExecuteScriptAsync("document.querySelector('#studio-queue-dialog').close();document.querySelector('#display-settings').click()");
                    await Wait(view, "!!document.querySelector('.display-settings-dialog[open]')");
                    await Capture(view, "display-settings.png");
                    await view.ExecuteScriptAsync("document.querySelector('[data-value=large]').click();document.querySelector('.display-settings-dialog').close()");
                    await Check(view, "document.body.dataset.textSize==='large'", "Large text selection failed");
                    form.ClientSize = new Size(1024, 768);
                    await Task.Delay(200);
                    await Capture(view, "prompt-1024-large.png");
                    await Check(view, "document.documentElement.scrollWidth<=innerWidth+1", "Large text overflows horizontally");
                    await Check(view, "window.testErrors.length===0", "JavaScript errors");
                    File.WriteAllText(Path.Combine(output, "result.json"), "{\"passed\":true,\"checks\":12,\"mode\":\"offline real frontend in WebView2 with 3440x1440 DPI acceptance\"}");
                    if (File.Exists(Path.Combine(output, "failure.txt"))) File.Delete(Path.Combine(output, "failure.txt"));
                    result = 0;
                }
                catch (Exception error)
                {
                    failure = error;
                }
                if (failure != null)
                {
                    string state = view.CoreWebView2 == null ? "uninitialized" : await view.ExecuteScriptAsync("JSON.stringify({errors:window.testErrors,text:document.body.innerText})");
                    File.WriteAllText(Path.Combine(output, "failure.txt"), failure + "\n" + state);
                }
                form.Close();
            };
            Application.Run(form);
        }
    }
    static async Task Wait(WebView2 view, string expression)
    {
        for (int i = 0; i < 100; i++) { if (await view.ExecuteScriptAsync(expression) == "true") return; await Task.Delay(100); }
        throw new Exception("Timed out: " + expression);
    }
    static async Task Check(WebView2 view, string expression, string message)
    {
        if (await view.ExecuteScriptAsync(expression) != "true") throw new Exception(message);
    }
    static async Task Capture(WebView2 view, string name)
    {
        using (FileStream file = File.Create(Path.Combine(output, name)))
            await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, file);
    }
}
