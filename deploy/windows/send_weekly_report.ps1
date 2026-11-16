# NEW PRODUCT へ週次レポート（週次売上分析_*.xlsx）を送る
# 2026-11-10 石原さんの追加修正依頼書 2-5・NEW PRODUCT ADR-138
#
# - 週次レポートのフォルダーにある「週次売上分析_YYYYMMDD-YYYYMMDD.xlsx」を NEW PRODUCT へ送る
# - NEW PRODUCT がまだ受け取っていないファイルだけ送る（同じファイルは送らない）。初回は過去分も全部送る
# - 「週次売上明細_….xlsx」（お客さまの入力が入る明細）は送らない
# - 「週次売上分析_….xlsx」も**そのままは送らない**。シート「新商品」の上の集計表と月別の表の値だけを CSV にして送る
#   （明細_全件のシート・新商品シートの下の明細＝メッセージ・付加情報を外へ出さない。2026-11-11 杉浦さんの指摘・十文字さんの決定）
#   Excel は**読むだけ**で、保存しない（2026-11-16: スクリプトから起動した Excel の保存が止まった。杉浦さんの調べ・ADR-156）。
#   同じファイルかどうかは元のファイルの指紋で NEW PRODUCT が判断する
# - 失敗したファイルは次の実行で自動で送り直す。結果はこのファイルと同じ場所の send_weekly_report.log に残す
# - 既存の weekly_report.ps1（レポート作成・保存・ChatWork 配信）には手を入れない。別のタスクとして動く
#
# 置き方: このファイルと np_feed_token.txt（鍵・1行）を同じフォルダーに置き、タスクスケジューラに登録する
#        （手順は同じフォルダーの README.txt）
param(
    [string]$Folder = '\\TERASTATION\share\目標管理\石原\★売上実績\週次レポート',
    [string]$Url = 'https://newproduct.fun-create.co.jp/api/feed/weekly-report',
    [string]$TokenFile = (Join-Path $PSScriptRoot 'np_feed_token.txt'),
    # 下の階層で探さないフォルダー（2026-11-11 杉浦さん: 「テスト」にテスト用の Excel があり、本物と同じ名前の形のものがあった）
    [string[]]$ExcludeDir = @('テスト'),
    [switch]$DryRun,
    # 送らずに、いちばん新しいファイルを Excel で読んで CSV にする所まで試す（2026-11-16: DryRun は Excel を通らず、止まる所を見つけられなかった）
    [switch]$TestRead
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$LogFile = Join-Path $PSScriptRoot 'send_weekly_report.log'
function Log([string]$m) {
    $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $m
    Write-Output $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

# 集計の部分だけを CSV（UTF-8）にする。Excel は読み取り専用で開き、保存しない
# （シート「新商品」の値だけ・「店名」か「メッセージ」の見出しの行から下は入れない）
function Get-SummaryCsv([string]$src) {
    $inv = [Globalization.CultureInfo]::InvariantCulture
    $xl = New-Object -ComObject Excel.Application
    $xl.Visible = $false; $xl.DisplayAlerts = $false
    $wb = $null
    try {
        $wb = $xl.Workbooks.Open($src, 0, $true)
        $ws = $null; foreach ($sh in $wb.Sheets) { if ($sh.Name -eq '新商品') { $ws = $sh; break } }
        if ($null -eq $ws) { throw 'シート「新商品」がありません' }
        $ur = $ws.UsedRange
        $nr = $ur.Rows.Count; $nc = $ur.Columns.Count; $top = $ur.Row; $left = $ur.Column
        if ($nr -lt 2) { throw 'シート「新商品」が空です' }
        $v = $ur.Value2
        $lines = New-Object System.Collections.Generic.List[string]
        for ($i = 1; $i -lt $top; $i++) { $lines.Add('') }
        for ($r = 1; $r -le $nr; $r++) {
            $cells = New-Object System.Collections.Generic.List[string]
            for ($i = 1; $i -lt $left; $i++) { $cells.Add('') }
            $stop = $false
            for ($c = 1; $c -le $nc; $c++) {
                $x = $v[$r, $c]
                if ($null -eq $x) { $t = '' }
                elseif ($x -is [int]) { $t = '' }                    # Value2 はエラーのセル（#DIV/0! など）を整数で返す。空にする
                elseif ($x -is [double]) { $t = $x.ToString('R', $inv) }
                else { $t = [string]$x }
                if ($r -ge 2 -and ($t -eq '店名' -or $t -eq 'メッセージ' -or $t -eq 'ﾒｯｾｰｼﾞ')) { $stop = $true; break }
                if ($t -match '[",\r\n]') { $t = '"' + $t.Replace('"', '""') + '"' }
                $cells.Add($t)
            }
            if ($stop) { break }
            $lines.Add([string]::Join(',', $cells))
        }
        $text = [string]::Join("`r`n", $lines) + "`r`n"
        $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($text)
        return ,$bytes                                   # , を付けないと PowerShell が配列をばらして返す
    } finally {
        if ($wb) { $wb.Close($false) }
        $xl.Quit()
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($xl)
        [System.GC]::Collect(); [System.GC]::WaitForPendingFinalizers()
    }
}

try {
    $tok = (Get-Content -Path $TokenFile -Raw).Trim()
} catch {
    Log ('鍵のファイルが読めません: ' + $TokenFile)
    exit 2
}
$h = @{ 'X-Feed-Token' = $tok }
try {
    $known = (Invoke-RestMethod -Uri ($Url + '/known') -Headers $h -Method Get -TimeoutSec 60).sha256
} catch {
    Log ('NEW PRODUCT に届きません（取り込み済みの一覧）: ' + $_.Exception.Message)
    exit 3
}
if ($null -eq $known) { $known = @() }

$files = Get-ChildItem -Path $Folder -Filter '週次売上分析_*.xlsx' -File -Recurse |
    Where-Object { $_.Name -notlike '~$*' } |
    Where-Object { $rel = $_.DirectoryName.Substring($Folder.TrimEnd('\').Length).Trim('\'); -not ($rel -split '\\' | Where-Object { $ExcludeDir -contains $_ }) } |
    Sort-Object LastWriteTime
if ($TestRead) {
    $f = $files | Select-Object -Last 1
    if ($null -eq $f) { Log '試し: 週次売上分析のファイルがありません'; exit 1 }
    $t0 = Get-Date
    $b = Get-SummaryCsv $f.FullName
    $n = ([System.Text.Encoding]::UTF8.GetString($b) -split "`r`n").Count - 1
    Log ('試し（送っていない）: ' + $f.Name + ' → ' + $n + ' 行・' + $b.Length + ' バイト・' + [int]((Get-Date) - $t0).TotalSeconds + ' 秒')
    exit 0
}
# 古い順に送る。同じ期間のファイルが2つあれば、後から保存したもの（訂正版）が最後に入って残る
$sent = 0; $skip = 0; $fail = 0
foreach ($f in $files) {
    $sha = (Get-FileHash -Path $f.FullName -Algorithm SHA256).Hash.ToLower()
    if ($known -contains $sha) { $skip++; continue }
    if ($DryRun) { Log ('送る予定: ' + $f.Name); $sent++; continue }
    try {
        $body = Get-SummaryCsv $f.FullName
        $hh = @{ 'X-Feed-Token' = $tok; 'X-File-Name' = [uri]::EscapeDataString($f.Name); 'X-Source-Sha256' = $sha;
                 'X-Payload' = 'newprod-summary-csv' }
        $r = Invoke-RestMethod -Uri $Url -Method Post -Headers $hh -Body $body `
            -ContentType 'text/csv; charset=utf-8' -TimeoutSec 120
        Log ('送った（集計だけ）: ' + $f.Name + ' → ' + $r.result)
        $known += $sha
        $sent++
    } catch {
        $msg = $_.Exception.Message
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $msg = $_.ErrorDetails.Message }
        Log ('失敗: ' + $f.Name + ' → ' + $msg)
        $fail++
    }
}
Log ('おわり: 送った ' + $sent + '・送らなかった（取り込み済み） ' + $skip + '・失敗 ' + $fail)
if ($fail -gt 0) { exit 1 }
exit 0
