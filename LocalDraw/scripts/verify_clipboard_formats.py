"""Validate Windows DataObject formats and cleanup without replacing the user's clipboard."""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parents[1]
prefix = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Windows.Forms,System.Drawing -TypeDefinition @'
using System.Drawing;
using System.Windows.Forms;
using System.Collections.Specialized;
public static class TestClipboard {
 public static IDataObject Data;
 public static uint Sequence;
 public static void SetDataObject(object data, bool copy, int retries, int delay) {
   Data=(IDataObject)data; Sequence++;
 }
 public static StringCollection GetFileDropList() {
   var files=new StringCollection(); files.AddRange((string[])Data.GetData(DataFormats.FileDrop)); return files;
 }
 public static bool ContainsImage() {return Data.GetDataPresent(DataFormats.Bitmap);}
 public static Image GetImage() {return (Image)Data.GetData(DataFormats.Bitmap);}
 public static bool ContainsData(string format) {return Data.GetDataPresent(format);}
 public static void Clear() {Sequence++;}
}
'@
"""
script = (root / 'scripts/copy_prompt_images.ps1').read_text(encoding='utf-8')
script = script.replace('[Windows.Forms.Clipboard]', '[TestClipboard]')
script = script.replace('[DllImport("user32.dll")]\n    public static extern uint GetClipboardSequenceNumber();',
                        'public static uint Sequence; public static uint GetClipboardSequenceNumber() {return Sequence++;}')
verify = r"""
    $copiedFiles=[TestClipboard]::GetFileDropList()
    if($copiedFiles.Count -ne $files.Count){throw 'Wrong number of copied files'}
    for($i=0;$i -lt $files.Count;$i++){
        if($copiedFiles[$i] -ne $files[$i]){throw 'Wrong file order'}
    }
    if($files.Count -eq 1){
        if(-not [TestClipboard]::ContainsImage()){throw 'Missing image format'}
        if(-not [TestClipboard]::ContainsData('PNG')){throw 'Missing original PNG format'}
    }
    Write-Output ('PASS: Windows DataObject '+$copiedFiles.Count+' original PNG file(s)')
"""
script = script.replace('    # Keep original files available', verify + '\n    # Keep original files available')
for count in (1, 2):
    cache = Path(tempfile.mkdtemp(prefix='local-draw-clipboard-'))
    files = []
    for i in range(count):
        file = cache / f'prompt-image-{i}.png'
        Image.new('RGB', (16, 16), 'blue').save(file)
        files.append(str(file))
    (cache / 'request.json').write_text(json.dumps({'files': files}), encoding='utf-8')
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-STA', '-Command', prefix + '\n' + script],
        env={**os.environ, 'STUDIO_IMAGE_CLIPBOARD_CACHE': str(cache)},
        capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    print(result.stdout.strip())
    if result.returncode:
        raise RuntimeError(result.stderr)
    assert not cache.exists(), 'Clipboard cache not cleaned'
