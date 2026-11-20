Unicode true
RequestExecutionLevel user
Name "LocalDraw 复制粘贴更新"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\LocalDraw"
Icon "windows\LocalDraw.ico"
!include "MUI2.nsh"
!define MUI_DIRECTORYPAGE_TEXT_TOP "请选择已安装 LocalDraw 的文件夹。请先关闭工作室，再点击安装。"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  SetRegView 64
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\LocalDraw4080" "InstallLocation"
  StrCmp $0 "" +2
    StrCpy $INSTDIR $0
FunctionEnd

Section
  IfFileExists "$INSTDIR\LocalDraw.exe" 0 invalid
  IfFileExists "$INSTDIR\web\direct-imports.js" 0 invalid
  IfFileExists "$INSTDIR\config\model-downloads.json" 0 invalid
  SetOutPath "$INSTDIR"
  File "..\LocalDraw.exe"
  SetOutPath "$INSTDIR\web"
  File "..\web\creative.js"
  File "..\web\direct-imports.js"
  File "..\web\studio-home.js"
  Goto done
  invalid:
    MessageBox MB_OK|MB_ICONSTOP "这个目录里没有找到对应版本的 LocalDraw。请选择包含 LocalDraw.exe 的安装目录。" /SD IDOK
    SetErrorLevel 1
    Abort
  done:
SectionEnd
