using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

internal sealed class PromptDraft
{
    public string hash, sourcePositive, sourceNegative, positive, negative;
}

internal sealed class DesktopDraft
{
    public string panel;
    public Dictionary<string, PromptDraft> prompts = new Dictionary<string, PromptDraft>();
}

internal sealed class PendingJob
{
    public string job, project, phase;
}

internal sealed class StudioWindow : Form
{
    private readonly StudioApi api = new StudioApi();
    private readonly Panel body = new Panel { Dock = DockStyle.Fill, Padding = new Padding(24, 16, 24, 14) };
    private readonly FlowLayoutPanel nav = Bar();
    private readonly TableLayoutPanel workspace = Grid(2);
    private readonly FlowLayoutPanel headerActions = Bar();
    private Action<bool> arrangePage;
    private bool wide, shellReady;
    private string recentProject, returnPage;
    private readonly bool smokeTest;
    private Button display;
    private Button homeReturn;
    private readonly Label status = Caption("");
    private readonly string storage = Path.Combine(StudioLauncher.DataRoot, @"data\native-editor");
    private readonly System.Windows.Forms.Timer saveTimer = new System.Windows.Forms.Timer { Interval = 400 };
    private Book book;
    private DesktopDraft draft = new DesktopDraft();
    private string page, dirtyFile, story = "";
    private int index, group, generation;
    private int previewRevision;
    private readonly SemaphoreSlim imageWorkers = new SemaphoreSlim(2);
    private bool busy, closing, watchingImages;
    private Task selections = Task.FromResult(0);
    private PendingJob pending;
    private readonly CancellationTokenSource lifetime = new CancellationTokenSource();
    private PictureBox picture;
    private StudioTheme.Candidate[] slots = new StudioTheme.Candidate[0];
    private TextBox pos, neg;
    private Button reviewFocus;
    private Button jobButton;
    private int quantity = 8;
    private const string ReaderKeys = "1–8  选图       ← →  翻页       Enter  下一页       ↓  写问题       Esc  结束输入";
    internal int Result { get; private set; }

    internal StudioWindow(bool smoke)
    {
        Text = StudioLauncher.Title;
        smokeTest = smoke;
        string fontFile = Path.Combine(storage,"display.txt"), recentFile = Path.Combine(storage,"last-project.txt");
        StudioTheme.Large = File.Exists(fontFile) && File.ReadAllText(fontFile) == "large";
        if (File.Exists(recentFile)) recentProject = File.ReadAllText(recentFile);
        AutoScaleDimensions = new SizeF(96, 96); AutoScaleMode = AutoScaleMode.Dpi;
        Font = StudioTheme.Body;
        BackColor = StudioTheme.Paper; ForeColor = StudioTheme.Ink;
        StartPosition = FormStartPosition.CenterScreen; ClientSize = new Size(1040, 740); MinimumSize = new Size(750, 610);
        WindowState = FormWindowState.Maximized;
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        var shell = Grid(1); shell.Dock = DockStyle.Fill;
        AutoRow(shell); StretchRow(shell); AutoRow(shell);
        var header = Grid(2); header.AutoSize = true; AutoRow(header);
        header.BackColor = StudioTheme.PanelColor; header.Padding = new Padding(24, 10, 18, 10);
        header.Paint += (s,e)=>{using(var p=new Pen(StudioTheme.Edge)) e.Graphics.DrawLine(p,0,header.Height-1,header.Width,header.Height-1);};
        header.ColumnStyles.Clear(); header.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        header.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        header.Controls.Add(StudioTheme.Brand(), 0, 0);
        headerActions.FlowDirection = FlowDirection.RightToLeft; header.Controls.Add(headerActions,1,0);
        display = Btn("界面显示", DisplayOptions); headerActions.Controls.Add(display);
        homeReturn=Btn("返回刚才的工作台",()=>Idle(ReturnToBook)); headerActions.Controls.Add(homeReturn);
        jobButton = Btn("查看生成任务", () => Run(ContinueJob)); headerActions.Controls.Add(jobButton);
        nav.BackColor = StudioTheme.Sidebar; nav.AutoSize = false; nav.AutoScroll = true;
        workspace.RowStyles.Add(new RowStyle(SizeType.Absolute,0)); StretchRow(workspace);
        workspace.Controls.Add(nav,0,0); workspace.Controls.Add(body,1,1);
        status.Font = StudioTheme.Small; status.Padding = new Padding(24, 0, 16, 7); status.ForeColor = StudioTheme.Muted;
        status.TextChanged += (s,e) => status.Visible = status.Text.Length > 0;
        status.Visible = false;
        shell.Controls.Add(header, 0, 0); shell.Controls.Add(workspace, 0, 1); shell.Controls.Add(status, 0, 2); Controls.Add(shell);
        workspace.SizeChanged += (s,e) => ArrangeShell();
        body.SizeChanged += (s,e)=>PageMargins();
        saveTimer.Tick += (s,e) => { try { Flush(); } catch (Exception ex) { status.Text = "草稿保存失败：" + ex.Message; } };
        FormClosing += async (s,e) => {
            if (!selections.IsCompleted) { e.Cancel = true; await selections; if (!closing) BeginInvoke(new Action(Close)); return; }
            try { Flush(); } catch (Exception ex) {
                if (MessageBox.Show(this, ex.Message + "\n仍要关闭窗口？", "草稿未保存", MessageBoxButtons.YesNo) != DialogResult.Yes) { e.Cancel = true; return; }
            }
            closing = true; lifetime.Cancel(); ++generation;
        };
        FormClosed += (s,e) => { saveTimer.Dispose(); ReleaseImages(); };
        string storyFile = Path.Combine(storage, "story.txt");
        if (File.Exists(storyFile)) story = File.ReadAllText(storyFile);
        string jobFile = Path.Combine(storage, "pending.json");
        if (!smoke && File.Exists(jobFile)) pending = StudioApi.Json().Deserialize<PendingJob>(File.ReadAllText(jobFile));
        ShowStory();
        Shown += (s,e) => {
            shellReady = true; ArrangeShell();
            var input = body.Controls.Find("StoryText", true).First();
            input.Focus();
            if (smoke) BeginInvoke(new Action(() => {
                bool ok = input is TextBox && input.CanSelect && input.IsHandleCreated;
                Result = ok ? 0 : 1;
                StudioApi.Write(Path.Combine(StudioLauncher.DataRoot, @"data\logs\desktop-smoke.json"),
                    StudioApi.Json().Serialize(new {native_textbox = ok, window_dpi = StudioLauncher.GetDpiForWindow(Handle),
                        per_monitor_v2 = StudioLauncher.AreDpiAwarenessContextsEqual(StudioLauncher.GetWindowDpiAwarenessContext(Handle), new IntPtr(-4))}));
                Close();
            }));
        };
    }
    private int Px(int logical) { return (int)Math.Round(logical * StudioLauncher.GetDpiForWindow(Handle) / 96d); }
    private void ArrangeShell()
    {
        if (!shellReady || closing || workspace.Width < 1) return;
        bool wideNow = workspace.Width >= Px(StudioTheme.Large ? 1580 : 1400) && workspace.Height >= Px(545);
        if (wide != wideNow || nav.Tag == null) {
            wide = wideNow; nav.Tag = wide;
            workspace.SuspendLayout();
            workspace.SetColumnSpan(nav,1); workspace.SetColumnSpan(body,1); workspace.SetRowSpan(nav,1);
            workspace.SetCellPosition(body,new TableLayoutPanelCellPosition(wide ? 1 : 0,1));
            workspace.ColumnStyles[0].SizeType = wide ? SizeType.Absolute : SizeType.Percent;
            workspace.ColumnStyles[0].Width = wide ? Px(208) : 100;
            workspace.ColumnStyles[1].SizeType = wide ? SizeType.Percent : SizeType.Absolute;
            workspace.ColumnStyles[1].Width = wide ? 100 : 0;
            workspace.RowStyles[0].Height = wide ? 0 : Math.Max(Px(48),StudioTheme.Body.Height+Px(26));
            workspace.SetRowSpan(nav,wide ? 2 : 1);
            nav.FlowDirection = wide ? FlowDirection.TopDown : FlowDirection.LeftToRight;
            nav.Padding = wide ? new Padding(Px(12),Px(24),Px(12),0) : new Padding(Px(12),2,0,0);
            RefreshNav(); workspace.ResumeLayout(true);
            if (arrangePage != null) arrangePage(wide);
        }
        PageMargins();
    }
    private void PageMargins()
    {
        if (!shellReady) return;
        int basePad = Px(wide ? 22 : 14);
        int side = wide && page != "reader" ? Math.Max(basePad,(body.ClientSize.Width-Px(page=="story"?1560:1240))/2) : basePad;
        int top = page=="story" && wide ? Math.Max(Px(32), Math.Min(Px(96),body.Height/11)) : Px(16);
        body.Padding = new Padding(side,top,side,Px(12));
    }
    private void DisplayOptions()
    {
        using (var dlg = Dialog("界面显示",360,105)) {
            var bar = Bar();
            foreach (bool large in new[]{false,true}) {
                bool value=large;
                bar.Controls.Add(Btn(large ? "大字号" : "标准字号", () => {
                    if (value != StudioTheme.Large) {
                        StudioApi.Write(Path.Combine(storage,"display.txt"), value ? "large" : "standard");
                        StudioTheme.Large = value; Font = StudioTheme.Body; status.Font = StudioTheme.Small;
                        display.Font = StudioTheme.Body; nav.Tag = null; ArrangeShell();
                        if (page=="reader") ShowReader(); else if(page=="board") ShowBoard(); else if(page=="summary") ShowSummary(); else ShowStory();
                    }
                    dlg.Close();
                },large==StudioTheme.Large));
            }
            dlg.Controls.Add(bar); dlg.ShowDialog(this);
        }
    }

    private static FlowLayoutPanel Bar()
    {
        return new FlowLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, WrapContents = false, Margin = Padding.Empty };
    }
    private static TableLayoutPanel Grid(int cols)
    {
        var grid = new TableLayoutPanel { ColumnCount = cols, Dock = DockStyle.Fill, Margin = Padding.Empty };
        for (int i = 0; i < cols; i++) grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f / cols));
        return grid;
    }
    private static void AutoRow(TableLayoutPanel g) { g.RowStyles.Add(new RowStyle(SizeType.AutoSize)); }
    private static void StretchRow(TableLayoutPanel g) { g.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); }
    private static Label Caption(string text) { return new Label { Text = text, AutoSize = true, Margin = new Padding(3, 5, 4, 5) }; }
    private static Label Heading(string text)
    {
        var l = Caption(text); l.Font = StudioTheme.Title; l.Margin = new Padding(0,0,0,8); return l;
    }
    private static Button Btn(string text, Action click, bool primary = false)
    {
        var b = new StudioTheme.StudioButton { Text = text, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
            Font = primary ? StudioTheme.Strong : StudioTheme.Body, Padding = new Padding(9, 3, 9, 3), Margin = new Padding(0,2,6,2) };
        StudioTheme.ButtonStyle(b, primary ? StudioTheme.Accent : StudioTheme.Raised, primary ? StudioTheme.OnAccent : StudioTheme.Ink,
            primary ? StudioTheme.Accent : StudioTheme.Edge);
        b.Click += (s,e) => click(); return b;
    }
    private static TextBox Area(string name, string value)
    {
        return new TextBox { Name = name, AccessibleName = name, Multiline = true, ScrollBars = ScrollBars.Vertical,
            Text = value, Dock = DockStyle.Fill, AcceptsReturn = true, BorderStyle = BorderStyle.None, WordWrap = true,
            Margin = Padding.Empty, BackColor = StudioTheme.Field, ForeColor = StudioTheme.Ink };
    }
    private static Panel Field(TextBox input, int inset)
    {
        var card = StudioTheme.Sheet(inset); card.Controls.Add(input);
        if(!input.ReadOnly){
            input.Enter+=(s,e)=>card.Invalidate(); input.Leave+=(s,e)=>card.Invalidate();
            card.Paint+=(s,e)=>{if(input.Focused)ControlPaint.DrawBorder(e.Graphics,card.ClientRectangle,StudioTheme.Accent,ButtonBorderStyle.Solid);};
        }
        return card;
    }
    private void NavItem(string label, string target, Action click)
    {
        var b = Btn(label, click);
        bool active = page == target;
        b.Padding = new Padding(7, 2, 7, 2); b.Font = active ? StudioTheme.Strong : StudioTheme.Small;
        if (wide) {
            b.AutoSize = false; b.Size = new Size(Px(182),Math.Max(Px(39),b.PreferredSize.Height));
            b.TextAlign = ContentAlignment.MiddleLeft; b.AutoEllipsis = true;
        }
        StudioTheme.ButtonStyle(b, active ? StudioTheme.Tint : StudioTheme.Sidebar, active ? StudioTheme.Accent : StudioTheme.Ink,
            active ? StudioTheme.Edge : StudioTheme.Sidebar);
        nav.Controls.Add(b);
    }
    private static void Clear(Control c)
    {
        while (c.Controls.Count != 0) { var child = c.Controls[0]; c.Controls.RemoveAt(0); child.Dispose(); }
    }
    private void BeginPage(string name)
    {
        Flush(); ++generation; ReleaseImages();
        if (page != "story" && name == "story") returnPage = page;
        page = name; picture = null; slots = new StudioTheme.Candidate[0]; pos = neg = null; reviewFocus = null;
        arrangePage = null; Clear(body); PageMargins(); RefreshNav(); status.Text = "";
    }
    private void RefreshNav()
    {
        nav.SuspendLayout(); Clear(nav);
        jobButton.Visible = pending != null;
        bool home=page=="story";
        homeReturn.Visible=home && book!=null;
        nav.Visible=!home;
        if(shellReady){
            workspace.ColumnStyles[0].Width=wide ? (home ? 0 : Px(208)) : 100;
            workspace.RowStyles[0].Height=wide||home ? 0 : Math.Max(Px(48),StudioTheme.Body.Height+Px(26));
        }
        if(home){nav.ResumeLayout(true);PageMargins();return;}
        if (wide) nav.Controls.Add(Caption("创作空间"));
        NavItem("创作首页","story", () => Idle(ShowStory));
        NavItem("打开作品","projects", () => Run(OpenProject));
        if (book != null)
        {
            if (wide) { var name = Caption(book.title); name.MaximumSize = new Size(Px(174),Px(62)); name.Font=StudioTheme.Strong;
                name.Margin = new Padding(4,Px(22),4,Px(8)); nav.Controls.Add(name); }
            NavItem("分镜", "board", () => Idle(ShowBoard));
            if (book.panels.Length > 0)
            {
                NavItem("逐格绘图", "reader", () => Idle(ShowReader));
                NavItem("问题汇总", "summary", () => Idle(ShowSummary));
            }
        }
        nav.ResumeLayout(true);
    }
    private void ReturnToBook()
    {
        if(returnPage=="summary" && book.panels.Length>0) ShowSummary();
        else if(returnPage=="reader" && book.panels.Length>0) ShowReader(); else ShowBoard();
    }
    private void Idle(Action fn) { if (!busy) { try { fn(); } catch (Exception e) { Error(e); } } }
    private async void Run(Func<Task> fn)
    {
        if (busy) return;
        busy = true;
        try { await selections; if (!closing) await fn(); }
        catch (Exception e) { if (!closing) Error(e); }
        finally { busy = false; }
    }
    private void Error(Exception e) { status.Text = e.Message; MessageBox.Show(this, e.Message, "工作室", MessageBoxButtons.OK, MessageBoxIcon.Information); }
    private void Changed(string filename) { dirtyFile = filename; saveTimer.Stop(); saveTimer.Start(); }
    private void Flush()
    {
        saveTimer.Stop();
        if (dirtyFile == null) return;
        StudioApi.Write(Path.Combine(storage, dirtyFile), dirtyFile == "story.txt" ? story : StudioApi.Json().Serialize(draft));
        dirtyFile = null;
    }
    private void ShowStory()
    {
        BeginPage("story");
        var grid = Grid(1); grid.RowStyles.Add(new RowStyle(SizeType.Absolute,300)); StretchRow(grid);
        var panes = Grid(2); StretchRow(panes); grid.Controls.Add(panes,0,0);
        var intro = Grid(1); intro.BackColor=StudioTheme.Clay; intro.Padding=new Padding(22);
        StretchRow(intro); AutoRow(intro); AutoRow(intro);
        var mark=new StudioTheme.StoryMark {Size=new Size(134,83),Anchor=AnchorStyles.Top|AnchorStyles.Left};
        intro.Controls.Add(mark,0,0);
        var headline=Heading("创作新漫画"); headline.Font=StudioTheme.Large ? StudioTheme.PageTitleLarge : StudioTheme.PageTitle;
        intro.Controls.Add(headline,0,1);
        var stages = Caption("故事   →   分镜   →   导出分镜"); stages.ForeColor=StudioTheme.Muted; stages.Margin=Padding.Empty;
        intro.Controls.Add(stages,0,2); panes.Controls.Add(intro,0,0);
        var sheet = StudioTheme.Sheet(24);
        var editor = Grid(1); AutoRow(editor); StretchRow(editor); AutoRow(editor);
        var top = Grid(2); top.AutoSize = true; AutoRow(top);
        var label = Caption("故事原文"); label.Font = StudioTheme.Strong; label.Margin = new Padding(0,0,0,13);
        var meter = Caption(story.Length + " / 12000"); meter.Font = StudioTheme.Small; meter.ForeColor = StudioTheme.Muted;
        meter.Anchor = AnchorStyles.Right; meter.Margin = new Padding(0,0,0,12);
        top.Controls.Add(label,0,0); top.Controls.Add(meter,1,0); editor.Controls.Add(top,0,0);
        var text = Area("StoryText", story); text.AccessibleName = "故事原文"; text.MaxLength = 12000;
        text.Font = StudioTheme.Manuscript;
        var writing = new Panel {Dock = DockStyle.Fill, Margin = Padding.Empty, BackColor = StudioTheme.Field};
        var hint = Caption("写下或粘贴故事，从第一幕开始……"); hint.Font = StudioTheme.Manuscript;
        hint.ForeColor = StudioTheme.Muted; hint.BackColor = StudioTheme.Field; hint.Location = new Point(3,1);
        hint.Click += (s,e) => text.Focus(); hint.Visible = story.Length == 0;
        writing.Controls.Add(hint); writing.Controls.Add(text); editor.Controls.Add(writing,0,1);
        text.TextChanged += (s,e) => { story = text.Text; hint.Visible = story.Length == 0; meter.Text = story.Length + " / 12000"; Changed("story.txt"); };
        var actions = Bar(); actions.FlowDirection = FlowDirection.RightToLeft;
        var footer = Grid(2); footer.AutoSize = true; footer.Padding = new Padding(0,10,0,0); AutoRow(footer);
        footer.Paint += (s,e) => { using(var pen = new Pen(StudioTheme.Edge)) e.Graphics.DrawLine(pen, 0, 0, footer.Width, 0); };
        var view = Caption("漫画视角   ·   第一人称 POV"); view.Font = StudioTheme.Small; view.ForeColor = StudioTheme.Muted;
        view.Anchor = AnchorStyles.Left; view.Margin = Padding.Empty; footer.Controls.Add(view,0,0);
        actions.Controls.Add(Btn("生成 POV 分镜", () => Run(async () => {
            if (pending != null) { await ContinueJob(); return; }
            if (story.Trim().Length < 5) { text.Focus(); throw new InvalidOperationException("请先写下至少五个字的故事。"); }
            Flush(); status.Text = "正在整理故事…";
            await Submit("story", null, new { action = "story_draft", text = story, mode = "structure", preferences = "漫画默认采用第一人称 POV。" });
        }), true));
        footer.Controls.Add(actions,1,0); editor.Controls.Add(footer,0,2);
        sheet.Controls.Add(editor); panes.Controls.Add(sheet,1,0);
        var library=Grid(1); AutoRow(library); StretchRow(library);
        grid.Controls.Add(library,0,1);
        var libraryHead=Grid(2);libraryHead.AutoSize=true;AutoRow(libraryHead);
        libraryHead.Controls.Add(Heading("继续已有作品"),0,0);
        var searchAll=Btn("打开作品",()=>Run(OpenProject)); searchAll.Anchor=AnchorStyles.Right;
        libraryHead.Controls.Add(searchAll,1,0); library.Controls.Add(libraryHead,0,0);
        var viewport=new Panel {Dock=DockStyle.Fill,AutoScroll=true,Margin=Padding.Empty};
        var books=Grid(1); books.AutoSize=true;books.AutoSizeMode=AutoSizeMode.GrowAndShrink;books.Dock=DockStyle.Top;
        viewport.Controls.Add(books); library.Controls.Add(viewport,0,1);
        var loading=Caption("正在读取作品…");books.Controls.Add(loading,0,0);
        int columns=1;
        Action layoutBooks=()=>{
            books.SuspendLayout();
            books.ColumnCount=columns;books.ColumnStyles.Clear();
            for(int c=0;c<columns;c++)books.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100f/columns));
            books.RowStyles.Clear();
            for(int i=0;i<books.Controls.Count;i++){
                if(i%columns==0)books.RowStyles.Add(new RowStyle(SizeType.Absolute,Px(StudioTheme.Large?90:78)));
                books.SetCellPosition(books.Controls[i],new TableLayoutPanelCellPosition(i%columns,i/columns));
            }
            books.ResumeLayout(true);
        };
        books.HandleCreated+=(s,e)=>books.BeginInvoke(new Action(async ()=>{
            if(books.IsDisposed || smokeTest)return;
            try{
                var saved=await ComicProjects();
                if(books.IsDisposed)return;
                Clear(books);
                foreach(var project in saved.Take(12)){
                    var entry=project;
                    var tile=new StudioTheme.BookButton(entry,recentProject==entry.id) {Margin=new Padding(0,0,Px(10),Px(8))};
                    tile.Click+=(sender,args)=>Run(()=>ResumeProject(entry.id)); books.Controls.Add(tile);
                }
                if(saved.Length==0)books.Controls.Add(Caption("还没有作品。完成第一份分镜后，就能在这里继续创作。"));
                layoutBooks();
            }catch(Exception ex){if(!books.IsDisposed){Clear(books);var error=Caption("暂时无法读取作品，可通过“打开作品”重试。");error.AccessibleDescription=ex.Message;books.Controls.Add(error);}}
        }));
        arrangePage = isWide => {
            grid.RowStyles[0].Height=Px(isWide?292:232);
            intro.Padding=new Padding(Px(isWide?32:16)); mark.Visible=isWide;
            headline.Font=isWide?(StudioTheme.Large?StudioTheme.PageTitleLarge:StudioTheme.PageTitle):StudioTheme.Title;
            panes.ColumnStyles[0].Width=isWide?31:29; panes.ColumnStyles[1].Width=100-panes.ColumnStyles[0].Width;
            library.Padding=new Padding(0,Px(isWide?38:18),0,0);libraryHead.Margin=new Padding(0,0,0,Px(10));
            columns=isWide?3:2;layoutBooks();
        };
        arrangePage(wide); body.Controls.Add(grid);
        text.Focus();
    }
    private async Task<ProjectItem[]> ComicProjects()
    {
        var available=await api.Get<ProjectItem[]>("/api/projects");
        return available.Where(p=>!p.quick_sample).OrderByDescending(p=>p.id==recentProject).ToArray();
    }
    private Panel ProjectShelf(Action<ProjectItem> open)
    {
        var shelf = StudioTheme.Sheet(14);
        var stack = Grid(1); AutoRow(stack); AutoRow(stack); AutoRow(stack); StretchRow(stack); AutoRow(stack);
        stack.Controls.Add(Heading("继续已有作品"),0,0); stack.Controls.Add(Caption("按作品名称搜索"),0,1);
        var search = new TextBox {Dock=DockStyle.Top,AccessibleName="搜索已有作品",BackColor=StudioTheme.Field,ForeColor=StudioTheme.Ink};
        stack.Controls.Add(search,0,2);
        var list = new ListBox {Dock=DockStyle.Fill, BorderStyle=BorderStyle.None, BackColor=StudioTheme.PanelColor,
            ForeColor=StudioTheme.Ink, IntegralHeight=false, AccessibleName="已有作品", Margin=new Padding(0,9,0,9),
            DrawMode=DrawMode.OwnerDrawFixed, ItemHeight=StudioTheme.Strong.Height+StudioTheme.Small.Height+12};
        list.DrawItem+=(s,e)=>{
            if(e.Index<0 || e.Index>=list.Items.Count) return;
            var project=(ProjectItem)list.Items[e.Index]; bool selected=(e.State & DrawItemState.Selected)!=0;
            using(var brush=new SolidBrush(selected?StudioTheme.Tint:StudioTheme.PanelColor))e.Graphics.FillRectangle(brush,e.Bounds);
            var line=new Rectangle(e.Bounds.Left+8,e.Bounds.Top+3,e.Bounds.Width-16,StudioTheme.Strong.Height+1);
            var flags=TextFormatFlags.NoPadding|TextFormatFlags.SingleLine|TextFormatFlags.EndEllipsis|TextFormatFlags.NoPrefix;
            TextRenderer.DrawText(e.Graphics,project.title,StudioTheme.Strong,line,StudioTheme.Ink,flags);
            line.Y+=line.Height+2;line.Height=StudioTheme.Small.Height+1;
            TextRenderer.DrawText(e.Graphics,(recentProject==project.id?"上次打开  ·  ":"")+project.pages+" 页分镜",StudioTheme.Small,line,StudioTheme.Muted,flags);
            e.DrawFocusRectangle();
        };
        stack.Controls.Add(list,0,3);
        var bar=Bar(); var go=Btn("继续",()=>{var p=list.SelectedItem as ProjectItem; if(p!=null)open(p);},true);
        var info=Caption("正在读取作品…"); info.ForeColor=StudioTheme.Muted; go.Enabled=false;
        bar.Controls.Add(go); bar.Controls.Add(info); stack.Controls.Add(bar,0,4);
        var projects = new ProjectItem[0];
        Action filter = () => {
            var matches = projects.Where(p=>p.title.IndexOf(search.Text.Trim(),StringComparison.CurrentCultureIgnoreCase)>=0).ToArray();
            list.DataSource=matches; go.Enabled=matches.Length>0; info.Text=matches.Length+" 个作品";
        };
        search.TextChanged += (s,e)=>filter();
        list.DoubleClick += (s,e)=>go.PerformClick();
        list.KeyDown += (s,e)=>{if(e.KeyCode==Keys.Enter){e.SuppressKeyPress=true;go.PerformClick();}};
        bool fetching=false;
        Func<Task> refresh = async () => {
            if(fetching || !shelf.Visible || smokeTest) return;
            fetching=true;
            try {
                var available=await ComicProjects();
                if(shelf.IsDisposed || closing) return;
                projects=available; filter();
            } catch(Exception ex) { if(!shelf.IsDisposed && !closing) {info.Text="读取失败，点击重试"; info.AccessibleDescription=ex.Message;} }
            finally {fetching=false;}
        };
        bool loaded=false;
        shelf.VisibleChanged += async (s,e)=>{if(shelf.Visible && shelf.IsHandleCreated && !loaded){loaded=true;await refresh();}};
        shelf.HandleCreated += (s,e)=>shelf.BeginInvoke(new Action(async ()=>{if(!shelf.IsDisposed && shelf.Visible && !loaded){loaded=true;await refresh();}}));
        info.Click += async (s,e)=>await refresh();
        shelf.Controls.Add(stack); return shelf;
    }
    private async Task OpenProject()
    {
        ProjectItem chosen=null;
        using (var dlg = Dialog("打开漫画作品", 500, 460))
        {
            dlg.Controls.Add(ProjectShelf(p=>{chosen=p;dlg.DialogResult=DialogResult.OK;}));
            if(dlg.ShowDialog(this)!=DialogResult.OK) return;
        }
        await ResumeProject(chosen.id);
    }
    private async Task ResumeProject(string id)
    {
        await LoadBook(id,null);
        if (!closing) { if (book.complete && book.panels.Length > 0) ShowReader(); else ShowBoard(); }
    }
    private async Task LoadBook(string id, string preferred)
    {
        Flush(); status.Text = "正在读取分镜…";
        var updated = await api.ReadBook(id);
        if (closing) return;
        Flush();
        book = updated;
        if(recentProject!=id) { StudioApi.Write(Path.Combine(storage,"last-project.txt"),id); recentProject=id; }
        var file = Path.Combine(storage, id + ".json");
        draft = File.Exists(file) ? StudioApi.Json().Deserialize<DesktopDraft>(File.ReadAllText(file)) : new DesktopDraft();
        string panel = preferred ?? draft.panel ?? book.last_panel;
        index = Math.Max(0, Array.FindIndex(book.panels, p => p.id == panel));
        group = -1;
    }
    private async Task Submit(string phase, string id, object request)
    {
        if (pending != null) throw new InvalidOperationException("请先查看上次生成任务。");
        var reply = await api.Act(request);
        SetPending(new PendingJob {phase = phase, project = id, job = (string)reply["job"]});
        if (!closing) await ContinueJob();
    }
    private void SetPending(PendingJob job)
    {
        var path = Path.Combine(storage, "pending.json");
        if (job == null) { if (File.Exists(path)) File.Delete(path); }
        else StudioApi.Write(path, StudioApi.Json().Serialize(job));
        pending = job;
        if (!closing && jobButton != null) jobButton.Visible = job != null;
    }
    private async Task<Dictionary<string,object>> WaitForJob(PendingJob work)
    {
        while (!closing)
        {
            var job = await api.Get<Dictionary<string,object>>("/api/job?id=" + StudioApi.Escape(work.job));
            if (closing) return null;
            string state = (string)job["status"];
            string message = job.ContainsKey("message") ? Convert.ToString(job["message"]) : state;
            status.Text = message;
            if (state == "done") return job;
            if (state == "paused")
            {
                if (MessageBox.Show(this, "这项任务已暂停。现在继续？", "生成任务", MessageBoxButtons.YesNo) != DialogResult.Yes) return null;
                var reply = await api.Act(new {action="resume_job", job=work.job});
                work.job = (string)reply["job"]; SetPending(work); continue;
            }
            if (state != "running" && state != "pausing" && state != "cancelling")
            {
                SetPending(null); throw new InvalidOperationException(message);
            }
            await Task.Delay(800, lifetime.Token);
        }
        return null;
    }

    // Keep the reader available while the GPU renders. Refresh only the book still on screen.
    private async void WatchImages(PendingJob work)
    {
        if (watchingImages) return;
        watchingImages = true;
        try {
            var job = await WaitForJob(work);
            if (job == null) return;
            while (busy && !closing) await Task.Delay(60, lifetime.Token);
            if (closing) return;
            busy = true;
            try {
                await selections;
                SetPending(null);
                if (book != null && book.id == work.project) {
                    string previousPage = page;
                    await LoadBook(work.project, book.panels.Length > 0 ? book.panels[index].id : null);
                    if (closing) return;
                    if (work.phase == "problems" && previousPage == "summary") {
                        index = Math.Max(0, Array.FindIndex(book.panels, p => p.marked)); ShowReader();
                    } else if (previousPage == "summary") ShowSummary();
                    else if (previousPage == "board") ShowBoard();
                    else ShowReader();
                }
                status.Text = (page == "reader" ? ReaderKeys + "      ·      " : "") + Convert.ToString(job["message"]);
            } finally { busy = false; }
        } catch (Exception error) { if (!closing) Error(error); }
        finally { watchingImages = false; }
    }

    private async Task ContinueJob()
    {
        var work = pending;
        if (work == null) return;
        if (work.phase == "draw" || work.phase == "problems") {
            if (!watchingImages && (book == null || book.id != work.project)) {
                await LoadBook(work.project, null); if (closing) return;
                if (work.phase == "problems") ShowSummary(); else ShowReader();
            }
            WatchImages(work); return;
        }
        var job = await WaitForJob(work);
        if (job == null || closing) return;
        if (work.phase == "story")
        {
            var result = (Dictionary<string,object>)job["result"];
            var created = await api.Act(new { action="create_story", result=result });
            string id = (string)created["project"]; SetPending(null);
            object original;
            if (result.TryGetValue("original", out original) && story.Trim() == Convert.ToString(original)) {
                story = ""; StudioApi.Write(Path.Combine(storage,"story.txt"),story);
            }
            await LoadBook(id, null);
            if (closing) return;
            ShowBoard(); await GenerateBoard();
        }
        else
        {
            SetPending(null); await LoadBook(work.project, null);
            if (!closing) ShowBoard();
        }
    }
    private Task GenerateBoard()
    {
        return Submit("board", book.id, new {action="storyboard_all",project=book.id,base_hash=book.hash,instruction="默认使用同一视点角色的第一人称 POV。"});
    }
    private void ShowBoard()
    {
        BeginPage("board"); var grid = Grid(1); AutoRow(grid); StretchRow(grid); AutoRow(grid);
        grid.Controls.Add(Heading(book.title + "  /  分镜"), 0, 0);
        var lines = new List<string>();
        foreach (Frame f in book.panels) { lines.Add(f.id + "  " + f.summary); lines.AddRange(f.dialogue); }
        lines.AddRange(book.draft);
        var text = new RichTextBox {Name="Storyboard", AccessibleName="分镜内容", Dock=DockStyle.Fill,
            BorderStyle=BorderStyle.None, ReadOnly=true, DetectUrls=false, BackColor=StudioTheme.PanelColor, ScrollBars=RichTextBoxScrollBars.Vertical};
        if (lines.Count == 0) lines.Add("故事已保存，可以生成分镜。");
        foreach (string line in lines) {
            var frame = System.Text.RegularExpressions.Regex.Match(line, @"^(P\d+-K\d+)\s+(.*)$");
            if (frame.Success) {
                if (text.TextLength > 0) text.AppendText("\n");
                text.SelectionFont = StudioTheme.Small; text.SelectionColor = StudioTheme.Muted;
                text.AppendText(frame.Groups[1].Value + "     ");
                text.SelectionFont = StudioTheme.Strong; text.SelectionColor = StudioTheme.Ink;
                text.AppendText(frame.Groups[2].Value + "\n");
            } else {
                text.SelectionFont = StudioTheme.Body; text.SelectionColor = StudioTheme.Muted;
                text.AppendText(line + "\n");
            }
        }
        text.Select(0,0); text.ScrollToCaret();
        var boardSheet = StudioTheme.Sheet(18); boardSheet.Controls.Add(text); grid.Controls.Add(boardSheet,0,1);
        var actions = Bar(); actions.Padding = new Padding(0,10,0,0); actions.FlowDirection = FlowDirection.RightToLeft;
        if (!book.complete && String.IsNullOrEmpty(book.bundle)) actions.Controls.Add(Btn("生成分镜", () => Run(GenerateBoard), true));
        else actions.Controls.Add(Btn(book.complete ? "导出分镜" : "确认并导出分镜", () => Run(Export), true));
        if (book.panels.Length > 0) actions.Controls.Add(Btn("进入逐格绘图", () => Idle(ShowReader)));
        grid.Controls.Add(actions, 0, 2); body.Controls.Add(grid);
    }
    private async Task Export()
    {
        using (var dlg = new SaveFileDialog { Filter = "分镜 JSON|*.json", FileName = book.id + "-分镜.json" })
        {
            if (dlg.ShowDialog(this) != DialogResult.OK) return;
            if (!book.complete)
            {
                await api.Act(new {action="accept_storyboard_bundle",project=book.id,bundle=book.bundle});
                await LoadBook(book.id, null);
            }
            await api.Export(book.id, dlg.FileName);
        }
        if (!closing) { ShowReader(); status.Text = ReaderKeys + "      ·      分镜已导出"; }
    }
    private async Task Import()
    {
        using (var dlg = new OpenFileDialog { Filter = "提示词 JSON|*.json" })
        {
            if (dlg.ShowDialog(this) != DialogResult.OK) return;
            if (new FileInfo(dlg.FileName).Length > 2000000) throw new InvalidDataException("提示词文件超过 2 MB。");
            object doc = StudioApi.Json().DeserializeObject(File.ReadAllText(dlg.FileName));
            var preview = await api.Act(new {action="preview_prompt_import",project=book.id,document=doc});
            if (!(bool)preview["valid"]) throw new InvalidDataException(String.Join("；", ((System.Collections.IEnumerable)preview["errors"]).Cast<string>()));
            var rows = ((System.Collections.IEnumerable)preview["panels"]).Cast<Dictionary<string,object>>().ToArray();
            if (MessageBox.Show(this, "使用文件中 " + rows.Length + " 格的提示词？", "导入提示词", MessageBoxButtons.OKCancel) != DialogResult.OK) return;
            await api.Act(new {action="save_prompt_import",project=book.id,document=doc,preview_hash=preview["preview_hash"],confirm=true});
            foreach (Dictionary<string,object> row in rows) draft.prompts.Remove((string)row["panel_id"]);
            Changed(book.id + ".json"); await LoadBook(book.id, book.panels[index].id); if (!closing) ShowReader();
        }
    }
    private static Form Dialog(string title, int width, int height)
    {
        return new Form { Text = title, StartPosition = FormStartPosition.CenterParent, ClientSize = new Size(width,height),
            Padding = new Padding(16), Font = StudioTheme.Body, BackColor = StudioTheme.Paper, ForeColor = StudioTheme.Ink,
            ShowInTaskbar = false, MinimizeBox = false, MaximizeBox = false };
    }
    private void ReleaseImages()
    {
        if (picture != null && picture.Image != null) { picture.Image.Dispose(); picture.Image = null; }
        foreach (var b in slots) if (b.Image != null) { b.Image.Dispose(); b.Image = null; }
    }
    private void ShowReader()
    {
        BeginPage("reader");
        var frames = book.panels;
        var f = frames[index];
        draft.panel = f.id; Changed(book.id + ".json");
        var layout=Grid(3); StretchRow(layout); layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        var grid = Grid(1); AutoRow(grid); StretchRow(grid); AutoRow(grid);
        var outline=StudioTheme.Sheet(12); var chapters=Grid(1); AutoRow(chapters); StretchRow(chapters);
        var contents=Caption("分镜目录");contents.Font=StudioTheme.Strong;contents.Margin=new Padding(4,3,4,12);
        chapters.Controls.Add(contents,0,0);
        var jump=new ListBox {Dock=DockStyle.Fill, BorderStyle=BorderStyle.None, BackColor=StudioTheme.PanelColor,
            ForeColor=StudioTheme.Ink, IntegralHeight=false, AccessibleName="分镜目录"};
        jump.ItemHeight=StudioTheme.Small.Height+StudioTheme.Body.Height+Px(14); jump.DrawMode=DrawMode.OwnerDrawFixed;
        jump.DrawItem+=(s,e)=>{
            if(e.Index<0) return;
            var item=frames[e.Index]; bool active=(e.State & DrawItemState.Selected)!=0;
            using(var brush=new SolidBrush(active ? StudioTheme.Tint : StudioTheme.PanelColor)) e.Graphics.FillRectangle(brush,e.Bounds);
            if(active)using(var brush=new SolidBrush(StudioTheme.Accent))e.Graphics.FillRectangle(brush,e.Bounds.X,e.Bounds.Y,Px(3),e.Bounds.Height);
            var r=new Rectangle(e.Bounds.X+Px(10),e.Bounds.Y+Px(5),e.Bounds.Width-Px(18),StudioTheme.Small.Height+1);
            var flags=TextFormatFlags.SingleLine|TextFormatFlags.EndEllipsis|TextFormatFlags.NoPadding|TextFormatFlags.NoPrefix;
            TextRenderer.DrawText(e.Graphics,item.id+(item.marked?"   ·   有问题":""),StudioTheme.Small,r,StudioTheme.Muted,flags);
            r.Y+=r.Height+2;r.Height=StudioTheme.Body.Height+2;
            TextRenderer.DrawText(e.Graphics,item.summary,active?StudioTheme.Strong:StudioTheme.Body,r,StudioTheme.Ink,flags);
            e.DrawFocusRectangle();
        };
        jump.Items.AddRange(frames.Select(item=>item.id+"  "+item.summary).ToArray());
        jump.SelectedIndex=index; jump.SelectedIndexChanged+=(s,e)=>{int target=jump.SelectedIndex;if(target>=0 && target!=index)Idle(()=>{index=target;group=-1;ShowReader();});};
        chapters.Controls.Add(jump,0,1); outline.Controls.Add(chapters);
        layout.Controls.Add(outline,0,0); layout.Controls.Add(grid,1,0);
        var head = Grid(2); head.AutoSize = true; AutoRow(head);
        head.ColumnStyles.Clear(); head.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100)); head.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        var title = Heading(book.title); title.Dock = DockStyle.Fill; title.AutoSize = false; title.AutoEllipsis = true;
        title.Height = StudioTheme.Title.Height + 6; title.MinimumSize = new Size(1, title.Height);
        head.Controls.Add(title,0,0);
        var position = Caption(String.Format("{0}  /  {1}     ·     {2}", index + 1, book.panels.Length, f.id));
        position.Anchor = AnchorStyles.Right; position.Font = StudioTheme.Strong; position.ForeColor = StudioTheme.Muted;
        head.Controls.Add(position,1,0); grid.Controls.Add(head,0,0);
        var canvas = Grid(3); canvas.ColumnStyles.Clear(); canvas.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        canvas.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); canvas.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        canvas.BackColor = StudioTheme.Stage; canvas.Padding = new Padding(12,8,12,8); StretchRow(canvas);
        var prev = Btn("← 上一页", () => Turn(-1)); prev.Enabled = index > 0; prev.Anchor = AnchorStyles.Left;
        var next = Btn(index + 1 == book.panels.Length ? "问题汇总 →" : "下一页 →", () => Turn(1)); next.Anchor = AnchorStyles.Right; reviewFocus = next;
        foreach (var button in new[]{prev,next}) {
            button.Margin = new Padding(2); button.Padding = new Padding(4,3,4,3); button.Font = StudioTheme.Small;
            StudioTheme.ButtonStyle(button, StudioTheme.Stage, StudioTheme.OnAccent, Color.FromArgb(122,112,102));
            button.FlatAppearance.MouseOverBackColor=Color.FromArgb(87,76,66);
        }
        var turnSize = new Size(Math.Max(prev.PreferredSize.Width,next.PreferredSize.Width), next.PreferredSize.Height);
        prev.AutoSize = next.AutoSize = false; prev.Size = next.Size = turnSize;
        picture = new PictureBox { Dock = DockStyle.Fill, BackColor = StudioTheme.Stage, SizeMode = PictureBoxSizeMode.Zoom, AccessibleName = "当前画格" };
        if (f.images.Length == 0) {
            var empty = new Label {Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter, ForeColor = StudioTheme.OnAccent,
                Text = "等待绘制" + Environment.NewLine + "填写提示词，画出这一格。", Font = StudioTheme.Body};
            picture.Controls.Add(empty);
        }
        canvas.Controls.Add(prev, 0, 0); canvas.Controls.Add(picture, 1, 0); canvas.Controls.Add(next, 2, 0);
        grid.Controls.Add(canvas, 0, 1);
        var strip = Bar(); strip.Anchor = AnchorStyles.None; strip.Dock = DockStyle.None;
        if (group < 0) group = Math.Max(0, Array.FindIndex(f.images, a => a.id == f.selected)) / 8;
        var visible = f.images.Skip(group * 8).Take(8).ToArray(); slots = new StudioTheme.Candidate[visible.Length];
        for (int i = 0; i < visible.Length; i++)
        {
            Art art = visible[i]; int slot = i;
            var pick = new StudioTheme.Candidate { Chosen = art.id == f.selected,
                Text = (art.id == f.selected ? "✓ " : "") + (i + 1), AccessibleName = "候选图 " + (i + 1) };
            if (art.verdict == "reject") pick.Text += " !";
            pick.Click += (s,e) => Choose(slot); strip.Controls.Add(pick); slots[i] = pick;
        }
        if (f.images.Length > 8) strip.Controls.Add(Btn("候选组 " + (group+1) + "/" + ((f.images.Length+7)/8), () => Idle(() => {group = (group+1)%((f.images.Length+7)/8); ShowReader();})));
        var rail = Grid(1); rail.AutoSize = true; AutoRow(rail); AutoRow(rail); rail.Controls.Add(strip,0,0);
        var description = Caption(f.summary); description.AutoSize = false; description.Dock = DockStyle.Fill;
        description.MinimumSize = new Size(1, StudioTheme.Body.Height + 4); description.Height = description.MinimumSize.Height;
        description.AutoEllipsis = true; description.Margin = new Padding(0,5,0,3); description.ForeColor = StudioTheme.Muted;
        rail.Controls.Add(description,0,1); grid.Controls.Add(rail, 0, 2);
        PromptDraft d; draft.prompts.TryGetValue(f.id, out d);
        bool use = d != null && d.hash == f.hash && d.sourcePositive == f.positive && d.sourceNegative == f.negative;
        var fields = Grid(2); fields.AutoSize = true; AutoRow(fields); fields.RowStyles.Add(new RowStyle(SizeType.Absolute, 61));
        var yes = Caption("正向提示词"); var no = Caption("负向提示词"); yes.Font = no.Font = StudioTheme.Strong;
        yes.Margin = new Padding(0,3,0,4); no.Margin = new Padding(5,3,0,4);
        fields.Controls.Add(yes,0,0); fields.Controls.Add(no,1,0);
        pos = Area("正向提示词", use ? d.positive : f.positive); neg = Area("负向提示词", use ? d.negative : f.negative);
        pos.MaxLength = neg.MaxLength = 20000;
        EventHandler edit = (s,e) => {
            draft.prompts[f.id] = new PromptDraft { hash=f.hash, sourcePositive=f.positive, sourceNegative=f.negative, positive=pos.Text, negative=neg.Text };
            Changed(book.id + ".json");
        };
        pos.TextChanged += edit; neg.TextChanged += edit;
        var left = Field(pos,7); left.Margin = new Padding(0,0,5,0);
        var right = Field(neg,7); right.Margin = new Padding(5,0,0,0);
        fields.Controls.Add(left,0,1); fields.Controls.Add(right,1,1);
        var toolbar = Grid(2); toolbar.AutoSize = true; AutoRow(toolbar); toolbar.Padding = new Padding(0,7,0,0);
        var utilities = Bar(); utilities.Controls.Add(Btn(f.marked ? "编辑问题 ↓" : "标记问题 ↓", () => Run(Note)));
        var import=Btn("导入提示词", () => Run(Import)); utilities.Controls.Add(import); toolbar.Controls.Add(utilities,0,0);
        var actions = Bar(); actions.Anchor = AnchorStyles.Right; actions.Dock = DockStyle.None; actions.Controls.Add(Caption("候选张数"));
        var count = new NumericUpDown {Minimum=1, Maximum=16, Value=quantity, Width=55, AccessibleName="候选张数"};
        count.Margin = new Padding(0,5,8,0);
        count.ValueChanged += (s,e) => quantity=(int)count.Value; actions.Controls.Add(count);
        actions.Controls.Add(Btn(f.images.Length == 0 ? "画这一格" : "重画这一格", () => Run(Draw), true));
        toolbar.Controls.Add(actions,1,0);
        var inspector=StudioTheme.Sheet(12);
        var editGrid=Grid(1); AutoRow(editGrid); AutoRow(editGrid); AutoRow(editGrid); AutoRow(editGrid); StretchRow(editGrid);
        var editTitle=Heading("本格绘图"); editGrid.Controls.Add(editTitle,0,0);
        editGrid.Controls.Add(fields,0,1); editGrid.Controls.Add(toolbar,0,2);
        var extras=Bar();
        var openScript=Btn("查看分镜与对白",()=>{using(var dlg=Dialog(f.id+" · 分镜与对白",530,330)){
            var preview=Area("分镜与对白",String.Join(Environment.NewLine+Environment.NewLine,new[]{f.summary}.Concat(f.dialogue)));
            preview.ReadOnly=true;dlg.Controls.Add(Field(preview,12));dlg.ShowDialog(this);
        }});
        extras.Controls.Add(Btn("导出分镜",()=>Run(Export)));
        editGrid.Controls.Add(extras,0,3); inspector.Controls.Add(editGrid); layout.Controls.Add(inspector,2,0);
        var script=Grid(1); script.Padding=new Padding(0,Px(20),0,0); AutoRow(script);StretchRow(script);
        var scriptHeading=Grid(2);scriptHeading.AutoSize=true;AutoRow(scriptHeading);
        var scriptTitle=Caption("本格分镜");scriptTitle.Font=StudioTheme.Strong;scriptHeading.Controls.Add(scriptTitle,0,0);
        openScript.Anchor=AnchorStyles.Right;openScript.Font=StudioTheme.Small;scriptHeading.Controls.Add(openScript,1,0);script.Controls.Add(scriptHeading,0,0);
        var scriptText=new RichTextBox{Dock=DockStyle.Fill,ReadOnly=true,TabStop=false,BorderStyle=BorderStyle.None,DetectUrls=false,
            AccessibleName="本格分镜与对白",ForeColor=StudioTheme.Muted, BackColor=StudioTheme.PanelColor,
            ScrollBars=RichTextBoxScrollBars.Vertical,Text=String.Join(Environment.NewLine+Environment.NewLine,new[]{f.summary}.Concat(f.dialogue))};
        script.Controls.Add(scriptText,0,1);editGrid.Controls.Add(script,0,4);
        arrangePage=isWide=>{
            layout.SuspendLayout(); fields.SuspendLayout(); toolbar.SuspendLayout();
            outline.Visible=isWide; editTitle.Visible=isWide;
            layout.ColumnStyles[0].SizeType=SizeType.Absolute; layout.ColumnStyles[0].Width=isWide?Px(216):0;
            layout.ColumnStyles[1].Width=100;
            layout.ColumnStyles[2].SizeType=SizeType.Absolute; layout.ColumnStyles[2].Width=isWide?Px(StudioTheme.Large ? 425 : 380):0;
            layout.SetCellPosition(inspector,new TableLayoutPanelCellPosition(isWide?2:1,isWide?0:1));
            grid.Margin=isWide?new Padding(Px(14),0,Px(14),0):Padding.Empty;
            inspector.Padding=new Padding(isWide?Px(16):Px(5));
            inspector.AutoSize=!isWide;
            editGrid.AutoSize=!isWide; script.Visible=isWide;
            description.Visible=!isWide; var importBar=isWide?extras:utilities;
            if(import.Parent!=importBar) {importBar.Controls.Add(import); if(isWide)importBar.Controls.SetChildIndex(import,0);}
            editGrid.RowStyles[1].SizeType=SizeType.AutoSize;
            editGrid.RowStyles[4].SizeType=isWide?SizeType.Percent:SizeType.Absolute;editGrid.RowStyles[4].Height=isWide?100:0;
            fields.AutoSize=true; fields.ColumnStyles[0].Width=isWide?100:50; fields.ColumnStyles[1].Width=isWide?0:50;
            fields.RowStyles.Clear(); AutoRow(fields);
            fields.RowStyles.Add(new RowStyle(SizeType.Absolute,Px(isWide?126:59)));
            fields.RowStyles.Add(new RowStyle(isWide?SizeType.AutoSize:SizeType.Absolute,0));
            fields.RowStyles.Add(new RowStyle(SizeType.Absolute,isWide?Px(83):0));
            fields.SetCellPosition(no,new TableLayoutPanelCellPosition(isWide?0:1,isWide?2:0));
            fields.SetCellPosition(right,new TableLayoutPanelCellPosition(isWide?0:1,isWide?3:1));
            toolbar.ColumnStyles[0].Width=isWide?100:50; toolbar.ColumnStyles[1].Width=isWide?0:50;
            toolbar.SetCellPosition(actions,new TableLayoutPanelCellPosition(isWide?0:1,isWide?1:0));
            extras.Visible=isWide; actions.Anchor=isWide?AnchorStyles.Left:AnchorStyles.Right;
            foreach(var slot in slots) slot.Size=new Size(Px(isWide?86:53),Px(isWide?73:46));
            toolbar.ResumeLayout(true); fields.ResumeLayout(true); layout.ResumeLayout(true);
        };
        arrangePage(wide); body.Controls.Add(layout); next.Focus();
        status.Text = ReaderKeys;
        int revision = generation;
        Art selected = visible.FirstOrDefault(a => a.id == f.selected) ?? visible.FirstOrDefault();
        if (selected != null) LoadArt(selected, null, revision);
        for (int i=0; i<visible.Length; i++) LoadArt(visible[i], slots[i], revision);
    }
    private async void LoadArt(Art art, Button thumb, int revision)
    {
        try
        {
            string root = Path.GetFullPath(Path.Combine(StudioLauncher.DataRoot, "data", "projects", book.id, "images")) + Path.DirectorySeparatorChar;
            string file = Path.GetFullPath(Path.Combine(root, art.file));
            if (!file.StartsWith(root, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("候选图片路径无效。");
            int limit = thumb == null ? Math.Max(1600,Math.Max(picture.Width,picture.Height)) : Math.Max(32, 2 * Math.Max(thumb.Width,thumb.Height));
            int preview = previewRevision;
            await imageWorkers.WaitAsync(lifetime.Token);
            Bitmap bitmap;
            try {
            if (closing || revision != generation || (thumb == null && preview != previewRevision)) return;
            bitmap = await Task.Run(() => {
                using (var stream = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var source = Image.FromStream(stream))
                {
                    double scale = Math.Min(1d, (double)limit / Math.Max(source.Width, source.Height));
                    var copy = new Bitmap(Math.Max(1,(int)(source.Width*scale)), Math.Max(1,(int)(source.Height*scale)));
                    using (var g = Graphics.FromImage(copy)) { g.InterpolationMode = InterpolationMode.HighQualityBilinear; g.DrawImage(source,0,0,copy.Width,copy.Height); }
                    return copy;
                }
            });
            } finally { imageWorkers.Release(); }
            if (closing || revision != generation || (thumb == null && preview != previewRevision)) bitmap.Dispose();
            else if (thumb != null) thumb.Image = bitmap;
            else { var previous = picture.Image; picture.Image = bitmap; if (previous != null) previous.Dispose(); }
        } catch (Exception ex) { if (!closing && revision == generation) status.Text = "图片读取失败：" + ex.Message; }
    }
    private void Turn(int step)
    {
        if (busy || index + step < 0) return;
        if (index + step >= book.panels.Length) { ShowSummary(); return; }
        index += step; group = -1; ShowReader();
    }
    private void Choose(int slot)
    {
        if (busy) return;
        Frame f = book.panels[index]; int n = group * 8 + slot;
        if (n >= f.images.Length) return;
        var art = f.images[n];
        selections = SaveChoice(selections, book.id, f, art);
    }
    private async Task SaveChoice(Task previous, string project, Frame f, Art art)
    {
        try {
            await previous;
            await api.Act(new {action="select_image",project=project,target=f.id,image=art.id,allow_rejected=art.verdict=="reject",allow_unreviewed=true,compact=true});
            f.selected=art.id;
            if (closing || page!="reader" || book.panels[index]!=f) return;
            for (int i=0; i<slots.Length; i++) {
                Art item=f.images[group*8+i];
                slots[i].Text=(item.id==art.id ? "✓ " : "")+(i+1)+(item.verdict=="reject" ? " !" : "");
                slots[i].Chosen = item.id==art.id;
                slots[i].Invalidate();
            }
            previewRevision++; LoadArt(art, null, generation);
        } catch (Exception e) { if (!closing) Error(e); }
    }
    private async Task Draw()
    {
        Frame f = book.panels[index]; Flush();
        if (String.IsNullOrWhiteSpace(pos.Text)) { pos.Focus(); throw new InvalidOperationException("请填写本格正向提示词。"); }
        var row = new {panel_id=f.id, page_id=f.id.Split('-')[0],panel_hash=f.hash, summary=f.summary,positive=pos.Text,negative=neg.Text};
        await Submit("draw",book.id,new {action="draw",project=book.id,target=f.id,base_hash=book.hash,force=true,
            confirm_prompts=true,prompt_rows=new[]{row},candidate_count=quantity});
    }
    private async Task Note()
    {
        Frame f = book.panels[index];
        var visible = page == "reader" ? f.images.Skip(group*8).Take(8) : f.images.AsEnumerable();
        var reference = visible.FirstOrDefault(a => a.id == f.selected) ?? visible.FirstOrDefault();
        if (!f.marked)
        {
            await api.Act(new {action="save_problem",project=book.id,panel_id=f.id,image_id=reference == null ? "" : reference.id});
            f.marked=true;
        }
        if (closing) return;
        using (var dlg = Dialog(f.id + " · 问题说明", 460, 195))
        {
            var input = Area("这格有什么问题", f.note ?? ""); input.MaxLength = 4000;
            var save = Btn(page == "summary" ? "保存问题" : "保存并继续", () => dlg.DialogResult=DialogResult.OK,true);
            var bar = Bar(); bar.Dock=DockStyle.Bottom; bar.Controls.Add(save); bar.Controls.Add(Caption("Enter 保存，Shift + Enter 换行"));
            dlg.Controls.Add(Field(input,12)); dlg.Controls.Add(bar); dlg.Shown += (s,e) => input.Focus();
            input.KeyDown += (s,e) => { if (e.KeyData==Keys.Enter) {e.SuppressKeyPress=true; dlg.DialogResult=DialogResult.OK;} };
            if (dlg.ShowDialog(this)==DialogResult.OK)
            {
                await api.Act(new {action="save_problem",project=book.id,panel_id=f.id,note=input.Text,image_id=reference == null ? "" : reference.id});
                f.note=input.Text;
                if (closing) return;
                if (page=="summary" || index+1==book.panels.Length) { ShowSummary(); return; }
                index++; group=-1;
            }
        }
        if (page=="summary") ShowSummary(); else ShowReader();
    }
    private void ShowSummary()
    {
        BeginPage("summary");
        var marked=book.panels.Where(p=>p.marked).ToArray();
        var grid=Grid(1); AutoRow(grid); StretchRow(grid); AutoRow(grid);
        grid.Controls.Add(Heading("问题汇总  ·  "+marked.Length+" 格"),0,0);
        var sheet = StudioTheme.Sheet(12); grid.Controls.Add(sheet,0,1);
        var list=new ListBox {Dock=DockStyle.Fill,DataSource=marked,AccessibleName="问题格子",IntegralHeight=false,BackColor=StudioTheme.PanelColor,
            BorderStyle=BorderStyle.None, DrawMode=DrawMode.OwnerDrawFixed, ItemHeight=StudioTheme.Strong.Height + StudioTheme.Small.Height + 16};
        if (marked.Length>0) {
            list.DrawItem += (s,e) => {
                if (e.Index < 0) return;
                Frame frame = marked[e.Index]; bool chosen = (e.State & DrawItemState.Selected) != 0;
                var bounds = e.Bounds;
                using (var brush = new SolidBrush(chosen ? StudioTheme.Tint : StudioTheme.PanelColor)) e.Graphics.FillRectangle(brush,bounds);
                var line = new Rectangle(bounds.Left+10,bounds.Top+5,bounds.Width-20,StudioTheme.Small.Height+1);
                TextRenderer.DrawText(e.Graphics,frame.id,StudioTheme.Small,line,StudioTheme.Muted,TextFormatFlags.NoPadding);
                line.Y += line.Height+2; line.Height = StudioTheme.Strong.Height+2;
                TextRenderer.DrawText(e.Graphics,String.IsNullOrWhiteSpace(frame.note) ? "待填写问题" : frame.note,StudioTheme.Strong,line,
                    StudioTheme.Ink,TextFormatFlags.NoPadding|TextFormatFlags.EndEllipsis|TextFormatFlags.SingleLine|TextFormatFlags.NoPrefix);
                e.DrawFocusRectangle();
            };
            sheet.Controls.Add(list);
        } else {
            list.Dispose();
            sheet.Controls.Add(new Label {Dock=DockStyle.Fill, TextAlign=ContentAlignment.MiddleCenter, ForeColor=StudioTheme.Muted,
                Text="没有已标记的问题。", Font=StudioTheme.Manuscript});
        }
        var actions=Bar(); actions.Padding = new Padding(0,10,0,0);
        actions.Controls.Add(Btn("← 返回审图",()=>Idle(ShowReader)));
        if(marked.Length>0)
        {
            Action current=()=>{index=Array.IndexOf(book.panels,(Frame)list.SelectedItem); group=-1;};
            list.DoubleClick+=(s,e)=>Idle(()=>{current();ShowReader();});
            actions.Controls.Add(Btn("编辑说明",()=>Run(async()=>{current();await Note();})));
            actions.Controls.Add(Btn("解除标记",()=>Run(async()=>{current();var f=book.panels[index];await api.Act(new{action="save_problem",project=book.id,panel_id=f.id,marked=false});f.marked=false;if(!closing)ShowSummary();})));
            actions.Controls.Add(Btn("按问题说明全部重画",()=>Run(async()=>{
                var empty=marked.FirstOrDefault(f=>String.IsNullOrWhiteSpace(f.note));
                if(empty!=null){list.SelectedItem=empty;current();await Note();return;}
                await Submit("problems",book.id,new{action="understand_and_redraw",project=book.id});
            }),true));
        }
        grid.Controls.Add(actions,0,2); body.Controls.Add(grid); status.Text=marked.Length>0 ? "双击问题格可返回该格选图。" : "";
    }
    protected override bool ProcessCmdKey(ref Message msg, Keys keys)
    {
        if (page=="reader")
        {
            if ((pos!=null && pos.Focused) || (neg!=null && neg.Focused))
            {
                if(keys==Keys.Escape){reviewFocus.Focus();return true;}
                return base.ProcessCmdKey(ref msg,keys);
            }
            if (ActiveControl is NumericUpDown || (ActiveControl!=null && ActiveControl.Parent is NumericUpDown)) return base.ProcessCmdKey(ref msg,keys);
            if(keys==Keys.Right || keys==Keys.Enter){Turn(1);return true;}
            if(keys==Keys.Left){Turn(-1);return true;}
            if(keys==Keys.Down){Run(Note);return true;}
            int slot=(int)keys-(int)Keys.D1;
            if(slot<0 || slot>7)slot=(int)keys-(int)Keys.NumPad1;
            if(slot>=0 && slot<8){Choose(slot);return true;}
        }
        if(page=="summary" && keys==Keys.Left){Idle(ShowReader);return true;}
        return base.ProcessCmdKey(ref msg,keys);
    }
}
