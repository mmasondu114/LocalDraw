Unicode true
RequestExecutionLevel user
Name "LocalDraw 漫画工作室"
OutFile "${STUB}"
InstallDir "$LOCALAPPDATA\Programs\LocalDraw"
Icon "windows\LocalDraw.ico"
CRCCheck off
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\LocalDraw.exe"
!define MUI_FINISHPAGE_RUN_TEXT "打开 LocalDraw"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Var PackagePath
Function .onInit
  ReadEnvStr $PackagePath "LOCALDRAW_INSTALL_SOURCE"
  IfFileExists "$PackagePath" +2
    Abort
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "此安装包需要 64 位 Windows。"
    Abort
  ${EndIf}
FunctionEnd

Section "LocalDraw 工作室"
  AddSize ${SIZE_KB}
  ; Extract to a new folder before publishing the application.
  FindFirst $0 $1 "$INSTDIR\*"
  loop:
    StrCmp $1 "" folderOK
    StrCmp $1 "." next
    StrCmp $1 ".." next
    FindClose $0
    MessageBox MB_ICONSTOP|MB_OK "请选择一个空文件夹安装，已有文件将保留。" /SD IDOK
    Abort
  next:
    FindNext $0 $1
    Goto loop
  folderOK:
    FindClose $0
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File /oname=7za.exe "${SEVENZIP}"
  SetOutPath "$INSTDIR"
  CreateDirectory "$INSTDIR\.localdraw-stage"
  DetailPrint "正在解压绘图环境和工作室，请稍候…"
  nsExec::ExecToLog '"$PLUGINSDIR\7za.exe" x -t7z -y -bso0 -bsp0 "-o$INSTDIR\.localdraw-stage" "$PackagePath"'
  Pop $0
  StrCmp $0 "0" extracted
  MessageBox MB_ICONSTOP|MB_OK "安装文件解压失败（$0），请检查磁盘空间或重新复制安装包。" /SD IDOK
  Goto failed
  extracted:
  IfFileExists "$INSTDIR\.localdraw-stage\LocalDraw.exe" +2
    Goto failed
  SetRegView 32
  ReadRegStr $0 HKLM "SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 checkVersion
  ReadRegStr $0 HKCU "SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  checkVersion:
  StrCmp $0 "" installWeb
  StrCmp $0 "0.0.0.0" installWeb webReady
  installWeb:
    DetailPrint "正在安装 Microsoft WebView2…"
    ExecWait '"$INSTDIR\.localdraw-stage\WebView2Offline.exe" /silent /install' $0
    StrCmp $0 0 webReady
    MessageBox MB_ICONSTOP|MB_OK "WebView2 安装失败（$0）。" /SD IDOK
    Goto failed
  webReady:
    Delete "$INSTDIR\.localdraw-stage\WebView2Offline.exe"
    ClearErrors
    FindFirst $0 $1 "$INSTDIR\.localdraw-stage\*"
  publish:
    StrCmp $1 "" published
    StrCmp $1 "." skip
    StrCmp $1 ".." skip
    ClearErrors
    Rename "$INSTDIR\.localdraw-stage\$1" "$INSTDIR\$1"
    IfErrors publishFailed
  skip:
    FindNext $0 $1
    Goto publish
  publishFailed:
    FindClose $0
    MessageBox MB_ICONSTOP|MB_OK "文件正在使用或目录不可写，请重新选择空目录。" /SD IDOK
    Goto failed
  published:
    FindClose $0
    RMDir "$INSTDIR\.localdraw-stage"
    WriteUninstaller "$INSTDIR\卸载 LocalDraw.exe"
    ${GetParameters} $0
    ClearErrors
    ${GetOptions} $0 "/NOINTEGRATION" $1
    IfErrors +2
      Goto done
    SetShellVarContext current
    SetOutPath "$INSTDIR"
    CreateShortcut "$DESKTOP\LocalDraw.lnk" "$INSTDIR\LocalDraw.exe"
    CreateShortcut "$SMPROGRAMS\LocalDraw.lnk" "$INSTDIR\LocalDraw.exe"
    SetRegView 64
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\LocalDraw4080" "DisplayName" "LocalDraw 漫画工作室（RTX 4080）"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\LocalDraw4080" "UninstallString" '"$INSTDIR\卸载 LocalDraw.exe"'
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\LocalDraw4080" "InstallLocation" "$INSTDIR"
    Goto done
  failed:
    RMDir /r "$INSTDIR\.localdraw-stage"
    SetErrorLevel 1
    Abort
  done:
SectionEnd

Section "Uninstall"
  IfFileExists "$INSTDIR\config\installed-runtime.json" +2
    Abort
  SetShellVarContext current
  SetRegView 64
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\LocalDraw4080" "InstallLocation"
  StrCmp $0 "$INSTDIR" 0 keepOtherInstall
  Delete "$DESKTOP\LocalDraw.lnk"
  Delete "$SMPROGRAMS\LocalDraw.lnk"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\LocalDraw4080"
  keepOtherInstall:
  Delete "$INSTDIR\LocalDraw.exe"
  Delete "$INSTDIR\LocalDraw.exe.config"
  Delete "$INSTDIR\launch.py"
  Delete "$INSTDIR\start_studio.py"
  RMDir /r "$INSTDIR\web"
  RMDir /r "$INSTDIR\src"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\comfy_nodes"
  RMDir /r "$INSTDIR\workflows"
  RMDir /r "$INSTDIR\schemas"
  RMDir /r "$INSTDIR\scripts"
  RMDir /r "$INSTDIR\config"
  Delete "$INSTDIR\卸载 LocalDraw.exe"
  RMDir "$INSTDIR"
SectionEnd

