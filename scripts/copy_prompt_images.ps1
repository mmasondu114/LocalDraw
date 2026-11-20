$ErrorActionPreference = 'Stop'
$cacheDir = [IO.Path]::GetFullPath($env:STUDIO_IMAGE_CLIPBOARD_CACHE)
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
if ([IO.Path]::GetDirectoryName($cacheDir).TrimEnd('\') -ne $tempRoot -or
    -not [IO.Path]::GetFileName($cacheDir).StartsWith('local-draw-clipboard-')) {
    throw 'Invalid clipboard cache directory'
}
try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public static class StudioClipboardSequence {
    [DllImport("user32.dll")]
    public static extern uint GetClipboardSequenceNumber();
}
'@
    $request = Get-Content -LiteralPath (Join-Path $cacheDir 'request.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $files = New-Object System.Collections.Specialized.StringCollection
    foreach ($file in $request.files) {
        $resolved = [IO.Path]::GetFullPath($file)
        if ([IO.Path]::GetDirectoryName($resolved) -ne $cacheDir -or
            [IO.Path]::GetExtension($resolved) -ne '.png') { throw 'Invalid image path' }
        [void]$files.Add($resolved)
    }
    $data = New-Object System.Windows.Forms.DataObject
    $data.SetFileDropList($files)
    $effect = [IO.MemoryStream]::new([byte[]](1,0,0,0))
    $data.SetData('Preferred DropEffect', $effect)
    $bitmap = $null
    $png = $null
    try {
        if ($files.Count -eq 1) {
            $bitmap = [Drawing.Image]::FromFile($files[0])
            $data.SetImage($bitmap)
            $png = [IO.MemoryStream]::new([IO.File]::ReadAllBytes($files[0]))
            $data.SetData('PNG', $false, $png)
        }
        [Windows.Forms.Clipboard]::SetDataObject($data, $true, 10, 100)
        $sequence = [StudioClipboardSequence]::GetClipboardSequenceNumber()
    } finally {
        if ($bitmap) { $bitmap.Dispose() }
        if ($png) { $png.Dispose() }
        $effect.Dispose()
    }
    [IO.File]::WriteAllText((Join-Path $cacheDir 'ready'), 'copied')
    # Keep original files available after the user refreshes or closes the studio.
    # Only the change counter is observed; clipboard contents are never read.
    while ([StudioClipboardSequence]::GetClipboardSequenceNumber() -eq $sequence) {
        Start-Sleep -Seconds 2
    }
} finally {
    # cacheDir was verified above as a dedicated child of the system temp folder.
    Remove-Item -LiteralPath $cacheDir -Recurse -Force -ErrorAction SilentlyContinue
}
