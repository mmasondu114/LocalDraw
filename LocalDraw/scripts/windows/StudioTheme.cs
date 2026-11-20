using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

internal static class StudioTheme
{
    internal static readonly Color Paper = ColorTranslator.FromHtml("#f6f3ee");
    internal static readonly Color PanelColor = ColorTranslator.FromHtml("#fffcf7");
    internal static readonly Color Raised = ColorTranslator.FromHtml("#f2ece4");
    internal static readonly Color Field = ColorTranslator.FromHtml("#fffcf7");
    internal static readonly Color Sidebar = ColorTranslator.FromHtml("#eee9e2");
    internal static readonly Color Ink = ColorTranslator.FromHtml("#302923");
    internal static readonly Color Muted = ColorTranslator.FromHtml("#6c5e53");
    internal static readonly Color Edge = ColorTranslator.FromHtml("#d2c7ba");
    internal static readonly Color Tint = ColorTranslator.FromHtml("#efe0d5");
    internal static readonly Color Accent = ColorTranslator.FromHtml("#88472f");
    internal static readonly Color OnAccent = ColorTranslator.FromHtml("#fffaf4");
    internal static readonly Color Stage = ColorTranslator.FromHtml("#3d3732");
    internal static readonly Color Clay = ColorTranslator.FromHtml("#ebd9c9");

    internal sealed class StoryMark : Control
    {
        internal StoryMark(){ DoubleBuffered=true; ResizeRedraw=true; TabStop=false; BackColor=Clay; }
        protected override void OnPaint(PaintEventArgs e)
        {
            var g=e.Graphics; g.SmoothingMode=SmoothingMode.AntiAlias;
            float scale=Math.Min(Width/132f,Height/83f); g.ScaleTransform(scale,scale);
            using(var outline=new Pen(Accent,1.7f))
            using(var paper=new SolidBrush(PanelColor))
            using(var shade=new SolidBrush(Color.FromArgb(220,172,145))) {
                g.TranslateTransform(14,13); g.RotateTransform(-8);
                g.FillRectangle(paper,1,0,80,55); g.DrawRectangle(outline,1,0,80,55);
                g.TranslateTransform(14,8); g.RotateTransform(12);
                g.FillRectangle(paper,0,0,80,54); g.DrawRectangle(outline,0,0,80,54);
                g.FillRectangle(shade,7,7,26,39); g.DrawRectangle(outline,7,7,26,39);
                g.DrawRectangle(outline,38,7,34,17); g.DrawRectangle(outline,38,29,34,17);
                g.DrawLine(outline,38,42,50,34); g.DrawLine(outline,50,34,64,45);
            }
        }
    }

    internal static bool Large;
    private static Font[] Fonts(float size, FontStyle style)
    {
        return new[]{new Font("Microsoft YaHei UI",size,style),new Font("Microsoft YaHei UI",size*1.2f,style)};
    }
    private static readonly Font[] small = Fonts(9,FontStyle.Regular), body = Fonts(10,FontStyle.Regular),
        strong = Fonts(10,FontStyle.Bold), title = Fonts(17,FontStyle.Bold), manuscript = Fonts(12,FontStyle.Regular);
    internal static Font Small { get { return small[Large ? 1 : 0]; } }
    internal static Font Body { get { return body[Large ? 1 : 0]; } }
    internal static Font Strong { get { return strong[Large ? 1 : 0]; } }
    internal static Font Title { get { return title[Large ? 1 : 0]; } }
    internal static Font Manuscript { get { return manuscript[Large ? 1 : 0]; } }
    internal static readonly Font PageTitle = new Font("Microsoft YaHei UI",26,FontStyle.Bold);
    internal static readonly Font PageTitleLarge = new Font("Microsoft YaHei UI",31,FontStyle.Bold);

    internal static void ButtonStyle(Button b, Color background, Color foreground, Color border)
    {
        b.FlatStyle = FlatStyle.Flat;
        b.UseVisualStyleBackColor = false;
        b.BackColor = background;
        b.ForeColor = foreground;
        b.FlatAppearance.BorderColor = border;
        b.FlatAppearance.MouseOverBackColor = background == Accent ? Color.FromArgb(126,71,47) : Tint;
        b.Cursor = Cursors.Hand;
    }

    internal static Panel Sheet(int padding)
    {
        var panel = new Panel { Dock = DockStyle.Fill, BackColor = PanelColor, Padding = new Padding(padding), Margin = Padding.Empty };
        panel.Paint += (s,e) => ControlPaint.DrawBorder(e.Graphics, panel.ClientRectangle, Edge, ButtonBorderStyle.Solid);
        return panel;
    }

    internal sealed class StudioButton : Button
    {
        private bool hover, down;
        internal StudioButton() { DoubleBuffered = true; }
        protected override void OnMouseEnter(EventArgs e) { hover=true; base.OnMouseEnter(e); Invalidate(); }
        protected override void OnMouseLeave(EventArgs e) { hover=down=false; base.OnMouseLeave(e); Invalidate(); }
        protected override void OnMouseDown(MouseEventArgs e) { down=true; base.OnMouseDown(e); Invalidate(); }
        protected override void OnMouseUp(MouseEventArgs e) { down=false; base.OnMouseUp(e); Invalidate(); }
        protected override void OnPaint(PaintEventArgs e)
        {
            if(SystemInformation.HighContrast || Width<10 || Height<10){base.OnPaint(e);return;}
            var g=e.Graphics;
            Color fill = Enabled && hover ? FlatAppearance.MouseOverBackColor : BackColor;
            if(down && Enabled) fill=ControlPaint.Dark(fill,.05f);
            g.Clear(Parent==null ? Paper : Parent.BackColor); g.SmoothingMode=SmoothingMode.AntiAlias;
            int d=Math.Min(Height-2,(int)(14*g.DpiX/96));
            using(var path=new GraphicsPath()) {
                path.AddArc(1,1,d,d,180,90); path.AddArc(Width-d-2,1,d,d,270,90);
                path.AddArc(Width-d-2,Height-d-2,d,d,0,90); path.AddArc(1,Height-d-2,d,d,90,90); path.CloseFigure();
                using(var brush=new SolidBrush(fill)) g.FillPath(brush,path);
                using(var pen=new Pen(Focused && ShowFocusCues ? Accent : FlatAppearance.BorderColor,Focused && ShowFocusCues ? 2 : 1))
                    g.DrawPath(pen,path);
            }
            var bounds=Rectangle.Inflate(ClientRectangle,-Math.Max(4,Padding.Left),-2);
            var align=TextAlign==ContentAlignment.MiddleLeft ? TextFormatFlags.Left : TextFormatFlags.HorizontalCenter;
            TextRenderer.DrawText(g,Text,Font,bounds,Enabled ? ForeColor : Muted,align|TextFormatFlags.NoPadding|
                TextFormatFlags.SingleLine|TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis|TextFormatFlags.NoPrefix);
            if(Focused && ShowFocusCues) ControlPaint.DrawFocusRectangle(g, Rectangle.Inflate(ClientRectangle,-4,-4),ForeColor,fill);
        }
    }

    internal static Control Brand()
    {
        var brand = new FlowLayoutPanel {AutoSize = true, WrapContents = false, Margin = new Padding(0, 3, 18, 0)};
        var mark = new Panel {Width = 28, Height = 28, BackColor = Accent, Margin = new Padding(0, 0, 7, 0)};
        mark.Paint += (s,e) => {
            var g = e.Graphics; g.ScaleTransform(mark.Width / 28f, mark.Height / 28f);
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            using (var pen = new Pen(OnAccent,1.7f)) {
                g.DrawRectangle(pen,6,6,16,16);
                g.DrawLine(pen,13,6,13,22); g.DrawLine(pen,13,14,22,14);
            }
        };
        brand.Controls.Add(mark);
        brand.Controls.Add(new Label {Text = "本地漫画工作室", AutoSize = true, Font = Strong, ForeColor = Ink, Margin = new Padding(0, 3, 0, 0)});
        return brand;
    }

    internal sealed class BookButton : Button
    {
        private readonly ProjectItem project;
        private readonly bool recent;
        private readonly Color binding;
        internal BookButton(ProjectItem item,bool lastOpened)
        {
            project=item; recent=lastOpened; Text=item.title; AccessibleName=item.title+"，"+item.pages+" 页分镜，继续创作";
            Dock=DockStyle.Fill; DoubleBuffered=true; FlatStyle=FlatStyle.Flat; Cursor=Cursors.Hand;
            var covers=new[]{Accent,Color.FromArgb(105,99,75),Color.FromArgb(148,103,69),Color.FromArgb(111,86,74)};
            uint hash=0; foreach(char c in item.id) hash=unchecked(hash*31+c);
            binding=covers[hash % covers.Length];
        }
        protected override void OnPaint(PaintEventArgs e)
        {
            var g=e.Graphics; float unit=g.DpiX/96f;
            bool hover=ClientRectangle.Contains(PointToClient(Cursor.Position));
            g.Clear(hover||Focused ? Raised : PanelColor);
            int x=(int)(15*unit), h=Math.Min(Height-12,(int)(45*unit)), w=h*3/4, y=(Height-h)/2;
            using(var brush=new SolidBrush(binding)) g.FillRectangle(brush,x,y,w,h);
            using(var pen=new Pen(Color.FromArgb(150,OnAccent),unit)) {
                g.DrawLine(pen,x+4*unit,y,x+4*unit,y+h);
                g.DrawRectangle(pen,x+w*.31f,y+h*.22f,w*.47f,h*.37f);
                g.DrawLine(pen,x+w*.31f,y+h*.45f,x+w*.78f,y+h*.36f);
                g.DrawLine(pen,x+w*.32f,y+h*.74f,x+w*.66f,y+h*.74f);
            }
            int left=x+w+(int)(14*unit);
            var line=new Rectangle(left,(Height-Strong.Height-Small.Height)/2-2,Math.Max(1,Width-left-(int)(32*unit)),Strong.Height+1);
            var flags=TextFormatFlags.NoPadding|TextFormatFlags.EndEllipsis|TextFormatFlags.SingleLine|TextFormatFlags.NoPrefix;
            TextRenderer.DrawText(g,Text,Strong,line,Ink,flags); line.Y+=line.Height+3; line.Height=Small.Height+2;
            TextRenderer.DrawText(g,(recent?"上次打开   ·   ":"")+project.pages+" 页分镜",Small,line,Muted,flags);
            int cx=Width-(int)(18*unit), cy=Height/2;
            g.SmoothingMode=SmoothingMode.AntiAlias;
            using(var p=new Pen(Muted,1.4f*unit)) g.DrawLines(p,new[]{new Point(cx-3,cy-4),new Point(cx+1,cy),new Point(cx-3,cy+4)});
            if(Focused && ShowFocusCues) ControlPaint.DrawFocusRectangle(g,Rectangle.Inflate(ClientRectangle,-3,-3),Ink,PanelColor);
        }
        protected override void OnMouseEnter(EventArgs e){base.OnMouseEnter(e);Invalidate();}
        protected override void OnMouseLeave(EventArgs e){base.OnMouseLeave(e);Invalidate();}
    }

    internal sealed class Candidate : Button
    {
        internal bool Chosen;
        internal Candidate()
        {
            Size = new Size(60, 53); Font = Small; Margin = new Padding(2, 6, 2, 2);
            ButtonStyle(this, Field, Ink, Edge); DoubleBuffered = true;
        }
        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            int inset = Math.Max(2, Height / 18);
            g.Clear(Chosen ? Accent : Field);
            var well = Rectangle.Inflate(ClientRectangle, -inset, -inset);
            if (Image != null && well.Width > 0 && well.Height > 0) {
                float scale = Math.Min(Image.Width / (float)well.Width, Image.Height / (float)well.Height);
                float w = well.Width * scale, h = well.Height * scale;
                g.DrawImage(Image,well,(Image.Width-w)/2,(Image.Height-h)/2,w,h,GraphicsUnit.Pixel);
            }
            var flags = TextFormatFlags.NoPadding | TextFormatFlags.SingleLine;
            var label = TextRenderer.MeasureText(g, Text, Font, Size.Empty, flags);
            var badge = new Rectangle(well.Left,well.Bottom-label.Height, label.Width+4,label.Height);
            using (var brush = new SolidBrush(Chosen ? Accent : Field)) g.FillRectangle(brush,badge);
            TextRenderer.DrawText(g, Text, Font, badge, Chosen ? OnAccent : Ink, flags | TextFormatFlags.HorizontalCenter);
            ControlPaint.DrawBorder(g,ClientRectangle,Chosen ? Accent : Edge,ButtonBorderStyle.Solid);
            if (Focused && ShowFocusCues) ControlPaint.DrawFocusRectangle(g,Rectangle.Inflate(ClientRectangle,-1,-1));
        }
    }

}
