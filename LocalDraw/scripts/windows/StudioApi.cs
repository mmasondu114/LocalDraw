using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

internal sealed class ProjectItem
{
    public string id { get; set; }
    public string title { get; set; }
    public bool quick_sample { get; set; }
    public int pages { get; set; }
    public override string ToString() { return title; }
}

internal sealed class Art
{
    public string id { get; set; }
    public string file { get; set; }
    public string verdict { get; set; }
}

internal sealed class Frame
{
    public string id { get; set; }
    public string hash { get; set; }
    public string summary { get; set; }
    public string positive { get; set; }
    public string negative { get; set; }
    public string[] dialogue { get; set; }
    public Art[] images { get; set; }
    public string selected { get; set; }
    public bool marked { get; set; }
    public string note { get; set; }
    public int count { get; set; }
    public override string ToString() { return id + "     " + (String.IsNullOrWhiteSpace(note) ? "待填写问题" : note); }
}

internal sealed class Book
{
    public string id { get; set; }
    public string title { get; set; }
    public string hash { get; set; }
    public Frame[] panels { get; set; }
    public string last_panel { get; set; }
    public bool complete { get; set; }
    public string bundle { get; set; }
    public string[] draft { get; set; }
}

internal sealed class StudioApi
{
    internal static JavaScriptSerializer Json() { return new JavaScriptSerializer { MaxJsonLength = Int32.MaxValue }; }
    internal static string Escape(string value) { return Uri.EscapeDataString(value); }
    private readonly object connection = new object();
    private string address, token;

    private string Send(string path, object body)
    {
        var request = (HttpWebRequest)WebRequest.Create(address + path);
        request.Proxy = null;
        request.AllowAutoRedirect = false;
        request.Timeout = 60000;
        if (body != null)
        {
            request.Method = "POST"; request.ContentType = "application/json; charset=utf-8";
            request.Headers["X-Studio-Token"] = token;
            byte[] data = Encoding.UTF8.GetBytes(Json().Serialize(body));
            using (var stream = request.GetRequestStream()) stream.Write(data, 0, data.Length);
        }
        using (var response = request.GetResponse())
        using (var reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8)) return reader.ReadToEnd();
    }

    private void Connect()
    {
        lock (connection)
        {
            if (token != null) return;
            address = StudioLauncher.ServiceAddress();
            string session;
            try { session = Send("/api/session", null); }
            catch (WebException error)
            {
                if (error.Response != null) { error.Response.Dispose(); throw new IOException("本地服务版本需要更新，请重新启动工作室服务。"); }
                StudioLauncher.StartServer();
                session = Send("/api/session", null);
            }
            token = Json().Deserialize<Dictionary<string,string>>(session)["token"];
        }
    }

    private string Request(string path, object body)
    {
        Connect();
        for (int attempt = 0; ; ++attempt)
        {
            try { return Send(path, body); }
            catch (WebException error)
            {
                if (error.Response == null) { lock (connection) token = null; throw new IOException("本地引擎连接中断，请重试。", error); }
                string message;
                using (var response = error.Response)
                using (var reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                    message = Json().Deserialize<Dictionary<string,string>>(reader.ReadToEnd())["error"];
                if (attempt == 0 && message == "请刷新本地界面后操作")
                {
                    lock (connection) token = null;
                    Connect();
                }
                else throw new InvalidOperationException(message);
            }
        }
    }

    internal Task<T> Get<T>(string path) { return Task.Run(() => Json().Deserialize<T>(Request(path, null))); }
    internal Task<Dictionary<string,object>> Act(object body)
    {
        return Task.Run(() => Json().Deserialize<Dictionary<string,object>>(Request("/api/action", body)));
    }
    internal Task<Book> ReadBook(string id) { return Get<Book>("/api/desktop-book?id=" + Escape(id)); }
    internal Task Export(string project, string file)
    {
        return Task.Run(() => Write(file, Request("/api/prompt-exchange?id=" + Escape(project), null)));
    }
    internal static void Write(string file, string value)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(file));
        string temp = file + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            File.WriteAllText(temp, value, new UTF8Encoding(false));
            if (File.Exists(file)) File.Replace(temp, file, null);
            else File.Move(temp, file);
        }
        finally { if (File.Exists(temp)) File.Delete(temp); }
    }
}
