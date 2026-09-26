; SecondX Windows installer (Inno Setup 6)
; Author: İlhan Koçaslan (Proaiml)
;
; Built by build.ps1:  ISCC /DAppVersion=x.y.z /DAgentDir=<PyInstaller onedir folder> secondx.iss
;
; Interactive: SecondX-Setup.exe
; Unattended:  SecondX-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /URL=http://influx:8086 /ORG=secondx /BUCKET=secondx
;                                /TOKENFILE=\\share\secondx.token [/HOST=WEB-01]
;              SecondX-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /LOCAL
;              (no parameters on an existing installation = upgrade, settings are kept)
; Remove:      "C:\Program Files\SecondX\unins000.exe" /VERYSILENT [/KEEPDATA]
;
; The installer only shows pages and copies files. Settings, the protected token file and the
; Scheduled Task are handled by "SecondX.exe --service install|uninstall" (winservice.py, unit tested).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AgentDir
  #define AgentDir "..\..\build\agent\SecondX"
#endif
#define Root "..\.."

[Setup]
AppId={{8F3C2A71-4D5B-4E9A-B6C1-2D7E9F0A5B34}
AppName=SecondX
AppVersion={#AppVersion}
AppVerName=SecondX {#AppVersion}
AppPublisher=İlhan Koçaslan (Proaiml)
AppPublisherURL=https://github.com/Proaiml/persecc
AppSupportURL=https://github.com/Proaiml/persecc/issues
AppCopyright=MIT License
DefaultDirName={autopf}\SecondX
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Root}\dist
OutputBaseFilename=SecondX-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile={#Root}\LICENSE
UninstallDisplayName=SecondX telemetry agent
UninstallDisplayIcon={app}\SecondX.exe
VersionInfoVersion={#AppVersion}
VersionInfoCompany=İlhan Koçaslan (Proaiml)
VersionInfoDescription=SecondX setup
VersionInfoProductName=SecondX
VersionInfoProductVersion={#AppVersion}
VersionInfoCopyright=MIT License - https://github.com/Proaiml/persecc
SetupLogging=yes
CloseApplications=no
#ifdef SignCommand
SignTool=secondx
SignedUninstaller=yes
#endif

[Languages]
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[InstallDelete]
; no stale files from an older version; SecondX-Setup.exe was the 2.3.0 uninstaller
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\SecondX-Setup.exe"

[Files]
Source: "{#AgentDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Root}\config.json"; DestDir: "{app}"; DestName: "config.default.json"; Flags: ignoreversion

[UninstallRun]
Filename: "{app}\SecondX.exe"; Parameters: "--service uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "SecondXService"

[CustomMessages]
tr.InfoTitle=SecondX neyi yapar, neyi yapmaz?
tr.InfoSub=Kurmadan önce lütfen okuyun.
tr.InfoText=SecondX her saniye bu sunucunun CPU, RAM, disk ve ağ kullanımını ve en çok kaynak tüketen süreçlerin ADLARINI ölçer.%n%nTOPLAMAZ: dosya içerikleri, komut satırları, kullanıcı adları, parolalar, ağ trafiğinin içeriği.%nVeri YALNIZCA sizin belirttiğiniz InfluxDB adresine gider (ya da bu bilgisayardaki dosyalara). Başka hiçbir yere bağlanmaz.%n%nKendi sınırları: tek çekirdeğin %%25'i ve 200 MB RAM. Aşarsa anında durur.%n%nKurulum şunları yapar:%n  • Program: Program Files\SecondX%n  • Ayarlar, token ve günlükler: ProgramData\SecondX (yalnızca SYSTEM ve Yöneticiler açabilir)%n  • Açılışta SYSTEM hesabıyla başlayan "SecondX" zamanlanmış görevi%n  • Uygulamalar listesinde kaldırma kaydı
tr.KeepTitle=Mevcut ayarlar
tr.KeepSub=Bu bilgisayarda SecondX ayarları zaten var.
tr.KeepText=Güncelleme yapıyorsanız mevcut ayarları korumanız önerilir. Diskte bekleyen veri her durumda korunur.
tr.KeepYes=Mevcut ayarları koru (önerilen)
tr.KeepNo=Yeni ayarları gir
tr.ModeTitle=Veriler nereye yazılsın?
tr.ModeSub=Ölçümlerin gönderileceği yer.
tr.ModeText=InfluxDB ile tüm sunucularınızı Grafana panosundan izleyebilirsiniz.
tr.ModeInflux=InfluxDB (önerilen)
tr.ModeLocal=Yalnızca bu bilgisayardaki dosyalara (ProgramData\SecondX\data)
tr.InfluxTitle=InfluxDB bağlantısı
tr.InfluxSub=İleri'ye bastığınızda bağlantı denenir.
tr.InfluxText=Ajana InfluxDB'de yalnızca bu bucket'a YAZMA yetkisi olan ayrı bir token vermeniz önerilir. Token ayar dosyasına yazılmaz; yalnızca SYSTEM ve Yöneticiler'in açabildiği ayrı bir dosyada saklanır.
tr.InfluxUrl=InfluxDB adresi:
tr.InfluxOrg=Organization (org):
tr.InfluxBucket=Bucket:
tr.InfluxToken=Token:
tr.HostTitle=Sunucu adı
tr.HostSub=Panoda bu sunucunun görüneceği ad.
tr.HostText=Boş bırakılırsa bilgisayar adı kullanılır.
tr.HostName=Sunucu adı:
tr.Required=Lütfen tüm alanları doldurun.
tr.TestOk=Bağlantı denendi: InfluxDB'ye yazma başarılı.
tr.TestUnreachable=InfluxDB adresine ulaşılamadı:
tr.TestAuth=InfluxDB token'ı reddetti (yetki yok). Token'ın bu bucket'a yazma yetkisi olmalı.
tr.TestNotFound=InfluxDB org ya da bucket'ı bulamadı. Adları kontrol edin.
tr.TestOther=InfluxDB beklenmeyen bir yanıt verdi:
tr.TestContinue=Yine de kuruluma devam edilsin mi? Ajan kurulur ve InfluxDB'ye ulaşana kadar veriyi diskte bekletir.
tr.ServiceFailed=Program dosyaları kuruldu ama ajan başlatılamadı:
tr.ServiceOk=SecondX çalışıyor.
tr.RemoveData=Ayarlar, günlükler ve diskte bekleyen veri de silinsin mi?%n%n%1
tr.PendingData=Dikkat: InfluxDB'ye henüz gönderilmemiş %1 veri bloğu var.
tr.ReadySettings=Ayarlar:
tr.ReadyKeep=Mevcut ayarlar korunacak
tr.ReadyLocal=Veri: bu bilgisayardaki dosyalar
tr.ReadyHost=Sunucu adı:
tr.BadParams=/URL için /TOKENFILE gerekli (ya da /LOCAL kullanın).

en.InfoTitle=What SecondX does and does not do
en.InfoSub=Please read before installing.
en.InfoText=Every second SecondX measures this server's CPU, RAM, disk and network usage and the NAMES of the processes that use the most resources.%n%nIt does NOT collect file contents, command lines, user names, passwords or network traffic contents.%nData goes ONLY to the InfluxDB address you enter (or to files on this computer). It connects nowhere else.%n%nOwn limits: 25%% of one CPU core and 200 MB RAM. It stops immediately if it exceeds them.%n%nSetup will:%n  • Program: Program Files\SecondX%n  • Settings, token and logs: ProgramData\SecondX (only SYSTEM and Administrators can open it)%n  • Scheduled Task "SecondX" that starts at boot as SYSTEM%n  • An entry in Apps & features to remove it
en.KeepTitle=Existing settings
en.KeepSub=SecondX settings already exist on this computer.
en.KeepText=When upgrading, keeping the existing settings is recommended. Data waiting on disk is always kept.
en.KeepYes=Keep the existing settings (recommended)
en.KeepNo=Enter new settings
en.ModeTitle=Where should the data go?
en.ModeSub=Destination of the measurements.
en.ModeText=With InfluxDB you can watch all your servers on the Grafana dashboard.
en.ModeInflux=InfluxDB (recommended)
en.ModeLocal=Only to files on this computer (ProgramData\SecondX\data)
en.InfluxTitle=InfluxDB connection
en.InfluxSub=The connection is tested when you click Next.
en.InfluxText=Give the agent its own token with WRITE permission to this bucket only. The token is not written to the settings file; it is stored in a separate file only SYSTEM and Administrators can open.
en.InfluxUrl=InfluxDB address:
en.InfluxOrg=Organization (org):
en.InfluxBucket=Bucket:
en.InfluxToken=Token:
en.HostTitle=Server name
en.HostSub=The name shown for this server on the dashboard.
en.HostText=Leave empty to use the computer name.
en.HostName=Server name:
en.Required=Please fill in all fields.
en.TestOk=Connection tested: writing to InfluxDB works.
en.TestUnreachable=Could not reach the InfluxDB address:
en.TestAuth=InfluxDB rejected the token (no permission). The token needs write permission to this bucket.
en.TestNotFound=InfluxDB could not find the org or bucket. Check the names.
en.TestOther=InfluxDB returned an unexpected response:
en.TestContinue=Continue the installation anyway? The agent is installed and keeps the data on disk until InfluxDB is reachable.
en.ServiceFailed=The program files were installed but the agent could not be started:
en.ServiceOk=SecondX is running.
en.RemoveData=Also delete the settings, logs and data waiting on disk?%n%n%1
en.PendingData=Note: %1 data block(s) have not been sent to InfluxDB yet.
en.ReadySettings=Settings:
en.ReadyKeep=Existing settings are kept
en.ReadyLocal=Data: files on this computer
en.ReadyHost=Server name:
en.BadParams=/URL needs /TOKENFILE (or use /LOCAL).

[Code]
var
  InfoPage: TOutputMsgWizardPage;
  KeepPage, ModePage: TInputOptionWizardPage;
  InfluxPage, HostPage: TInputQueryWizardPage;
  ServiceReport: String;

function DataDir: String;
begin
  Result := ExpandConstant('{commonappdata}\SecondX');
end;

function HasExistingConfig: Boolean;
begin
  Result := FileExists(DataDir + '\config.json');
end;

function HasParam(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
      Result := True;
end;

function Param(const Name: String): String;
begin
  Result := Trim(ExpandConstant('{param:' + Name + '|}'));
end;

{ ---------- effective settings: wizard pages, or command-line parameters in silent mode ---------- }
function KeepConfig: Boolean;
begin
  if WizardSilent then
    Result := HasExistingConfig and (Param('URL') = '') and not HasParam('/LOCAL')
  else
    Result := HasExistingConfig and KeepPage.Values[0];
end;

function LocalMode: Boolean;
begin
  if WizardSilent then
    Result := HasParam('/LOCAL') or (Param('URL') = '')
  else
    Result := ModePage.Values[1];
end;

function SettingUrl: String;
begin
  if WizardSilent then Result := Param('URL') else Result := Trim(InfluxPage.Values[0]);
end;

function SettingOrg: String;
begin
  if WizardSilent then begin
    Result := Param('ORG');
    if Result = '' then Result := 'secondx';
  end else Result := Trim(InfluxPage.Values[1]);
end;

function SettingBucket: String;
begin
  if WizardSilent then begin
    Result := Param('BUCKET');
    if Result = '' then Result := 'secondx';
  end else Result := Trim(InfluxPage.Values[2]);
end;

function SettingHost: String;
begin
  if WizardSilent then Result := Param('HOST') else Result := Trim(HostPage.Values[0]);
end;

{ ---------- connection test (the same test write as "SecondX.exe --check") ---------- }
function UrlEncode(const S: String): String;
var
  I: Integer;
  C: Char;
begin
  Result := '';
  for I := 1 to Length(S) do begin
    C := S[I];
    if ((C >= 'a') and (C <= 'z')) or ((C >= 'A') and (C <= 'Z')) or ((C >= '0') and (C <= '9')) or
       (C = '-') or (C = '_') or (C = '.') or (C = '~') then
      Result := Result + C
    else
      Result := Result + '%' + Format('%.2x', [Ord(C)]);
  end;
end;

function EscapeTag(const S: String): String;
begin
  Result := S;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, ' ', '\ ', True);
  StringChangeEx(Result, ',', '\,', True);
  StringChangeEx(Result, '=', '\=', True);
end;

function TestInflux(const Url, Org, Bucket, Token: String; var Msg: String): Boolean;
var
  Http: Variant;
  Base: String;
  Status: Integer;
begin
  Result := False;
  Base := Url;
  while (Length(Base) > 0) and (Base[Length(Base)] = '/') do
    Delete(Base, Length(Base), 1);
  try
    Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Http.SetTimeouts(5000, 5000, 5000, 5000);
    Http.Open('POST', Base + '/api/v2/write?org=' + UrlEncode(Org) + '&bucket=' + UrlEncode(Bucket) + '&precision=s', False);
    Http.SetRequestHeader('Authorization', 'Token ' + Token);
    Http.SetRequestHeader('Content-Type', 'text/plain; charset=utf-8');
    Http.Send('secondx_check,host=' + EscapeTag(GetComputerNameString) + ' ok=1');
    Status := Http.Status;
    if (Status = 204) or (Status = 200) then begin
      Result := True;
      Msg := CustomMessage('TestOk');
    end else if (Status = 401) or (Status = 403) then
      Msg := CustomMessage('TestAuth')
    else if Status = 404 then
      Msg := CustomMessage('TestNotFound')
    else
      Msg := CustomMessage('TestOther') + ' HTTP ' + IntToStr(Status) + #13#10 + Copy(Http.ResponseText, 1, 300);
  except
    Msg := CustomMessage('TestUnreachable') + ' ' + Base + #13#10#13#10 + GetExceptionMessage;
  end;
end;

{ ---------- wizard ---------- }
procedure InitializeWizard;
begin
  InfoPage := CreateOutputMsgPage(wpLicense, CustomMessage('InfoTitle'), CustomMessage('InfoSub'), CustomMessage('InfoText'));

  KeepPage := CreateInputOptionPage(InfoPage.ID, CustomMessage('KeepTitle'), CustomMessage('KeepSub'),
    CustomMessage('KeepText'), True, False);
  KeepPage.Add(CustomMessage('KeepYes'));
  KeepPage.Add(CustomMessage('KeepNo'));
  KeepPage.Values[0] := True;

  ModePage := CreateInputOptionPage(KeepPage.ID, CustomMessage('ModeTitle'), CustomMessage('ModeSub'),
    CustomMessage('ModeText'), True, False);
  ModePage.Add(CustomMessage('ModeInflux'));
  ModePage.Add(CustomMessage('ModeLocal'));
  ModePage.Values[0] := True;

  InfluxPage := CreateInputQueryPage(ModePage.ID, CustomMessage('InfluxTitle'), CustomMessage('InfluxSub'),
    CustomMessage('InfluxText'));
  InfluxPage.Add(CustomMessage('InfluxUrl'), False);
  InfluxPage.Add(CustomMessage('InfluxOrg'), False);
  InfluxPage.Add(CustomMessage('InfluxBucket'), False);
  InfluxPage.Add(CustomMessage('InfluxToken'), True);
  InfluxPage.Values[0] := 'http://localhost:8086';
  InfluxPage.Values[1] := 'secondx';
  InfluxPage.Values[2] := 'secondx';

  HostPage := CreateInputQueryPage(InfluxPage.ID, CustomMessage('HostTitle'), CustomMessage('HostSub'),
    CustomMessage('HostText'));
  HostPage.Add(CustomMessage('HostName'), False);
  HostPage.Values[0] := GetComputerNameString;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if PageID = KeepPage.ID then
    Result := not HasExistingConfig
  else if (PageID = ModePage.ID) or (PageID = HostPage.ID) then
    Result := KeepConfig
  else if PageID = InfluxPage.ID then
    Result := KeepConfig or LocalMode;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Msg: String;
begin
  Result := True;
  if CurPageID = InfluxPage.ID then begin
    if (Trim(InfluxPage.Values[0]) = '') or (Trim(InfluxPage.Values[1]) = '') or
       (Trim(InfluxPage.Values[2]) = '') or (Trim(InfluxPage.Values[3]) = '') then begin
      MsgBox(CustomMessage('Required'), mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if TestInflux(SettingUrl, SettingOrg, SettingBucket, Trim(InfluxPage.Values[3]), Msg) then
      MsgBox(Msg, mbInformation, MB_OK)
    else
      Result := MsgBox(Msg + #13#10#13#10 + CustomMessage('TestContinue'), mbConfirmation, MB_YESNO) = IDYES;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo,
  MemoGroupInfo, MemoTasksInfo: String): String;
var
  S: String;
begin
  S := MemoDirInfo + NewLine + NewLine + CustomMessage('ReadySettings') + NewLine;
  if KeepConfig then
    S := S + Space + CustomMessage('ReadyKeep') + NewLine
  else begin
    if LocalMode then
      S := S + Space + CustomMessage('ReadyLocal') + NewLine
    else
      S := S + Space + 'InfluxDB: ' + SettingUrl + '  (org ' + SettingOrg + ', bucket ' + SettingBucket + ')' + NewLine;
    S := S + Space + CustomMessage('ReadyHost') + ' ' + SettingHost + NewLine;
  end;
  Result := S;
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  if WizardSilent and (Param('URL') <> '') and (Param('TOKENFILE') = '') and not HasParam('/LOCAL') then begin
    Log(CustomMessage('BadParams'));
    MsgBox(CustomMessage('BadParams'), mbError, MB_OK);
    Result := False;
  end;
end;

{ Upgrade: stop the running agent before its files are replaced }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  RC: Integer;
begin
  { ending the task stops the supervisor; the agent notices within ~1 s and saves unsent data to disk }
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/End /TN SecondX', '', SW_HIDE, ewWaitUntilTerminated, RC);
  Sleep(3000);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM SecondX.exe', '', SW_HIDE, ewWaitUntilTerminated, RC);
  { uninstall entry of the 2.3.0 setup, replaced by this installer's own entry }
  RegDeleteKeyIncludingSubkeys(HKLM64, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\SecondX');
  Result := '';
end;

function Q(const S: String): String;
begin
  Result := AddQuotes(S);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Params, TokenFile, ReportFile: String;
  Lines: TArrayOfString;
  I, RC: Integer;
begin
  if CurStep <> ssPostInstall then
    Exit;
  ReportFile := ExpandConstant('{tmp}\secondx-report.txt');
  Params := '--service install --report-file ' + Q(ReportFile);
  if KeepConfig then
    Params := Params + ' --keep-config'
  else begin
    if SettingHost <> '' then
      Params := Params + ' --host ' + Q(SettingHost);
    if LocalMode then
      Params := Params + ' --local'
    else begin
      if WizardSilent then
        TokenFile := Param('TOKENFILE')
      else begin
        { the token goes to the agent through a file in this setup's private temp folder, never on a command line }
        TokenFile := ExpandConstant('{tmp}\secondx-token.txt');
        SaveStringToFile(TokenFile, Trim(InfluxPage.Values[3]), False);
      end;
      Params := Params + ' --url ' + Q(SettingUrl) + ' --org ' + Q(SettingOrg) + ' --bucket ' + Q(SettingBucket) +
                ' --token-file ' + Q(TokenFile);
      if not WizardSilent then
        Params := Params + ' --delete-token-file';
    end;
  end;
  WizardForm.StatusLabel.Caption := 'SecondX...';
  Exec(ExpandConstant('{app}\SecondX.exe'), Params, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, RC);
  ServiceReport := '';
  if LoadStringsFromFile(ReportFile, Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do
      ServiceReport := ServiceReport + Lines[I] + #13#10;
  Log('SecondX --service install (exit ' + IntToStr(RC) + '):' + #13#10 + ServiceReport);
  if RC <> 0 then begin
    MsgBox(CustomMessage('ServiceFailed') + #13#10#13#10 + ServiceReport, mbError, MB_OK);
    ServiceReport := CustomMessage('ServiceFailed') + #13#10 + ServiceReport;
  end else
    ServiceReport := CustomMessage('ServiceOk') + #13#10#13#10 + ServiceReport;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and (ServiceReport <> '') then
    WizardForm.FinishedLabel.Caption := ServiceReport;
end;

{ ---------- uninstall ---------- }
function PendingBlocks: Integer;
var
  F: TFindRec;
begin
  Result := 0;
  if FindFirst(DataDir + '\spool\block-*', F) then begin
    try
      repeat
        Result := Result + 1;
      until not FindNext(F);
    finally
      FindClose(F);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Remove: Boolean;
  Note: String;
begin
  if (CurUninstallStep <> usPostUninstall) or not DirExists(DataDir) then
    Exit;
  if UninstallSilent then
    Remove := not HasParam('/KEEPDATA')
  else begin
    Note := '';
    if PendingBlocks > 0 then
      Note := FmtMessage(CustomMessage('PendingData'), [IntToStr(PendingBlocks)]);
    Remove := MsgBox(FmtMessage(CustomMessage('RemoveData'), [Note]), mbConfirmation, MB_YESNO) = IDYES;
  end;
  if Remove then
    DelTree(DataDir, True, True, True);
end;
