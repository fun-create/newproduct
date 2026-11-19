# NEW PRODUCT → 週次レポートの新商品マスタ（新商品マスタ.xlsx）に、発売した商品の1行を足す
# 2026-11-10 石原さんの追加修正依頼書 4-4・NEW PRODUCT ADR-139（つなぎ。本来は CIP の共通商品コードで数える）
#
# - NEW PRODUCT で「週次レポートに載せる」を押した商品（商品ラベル・含む／除外キーワード・月の目標）を受け取る
# - sales.csv で、その条件に当たる明細の数（発売日から）と、同じ明細に当たる既存の行（二重計上のおそれ）を数える
# - 重なりが無ければ、控え（新商品マスタ_backup_YYYYMMDD_HHMMSS.xlsx）を取ってから、シート「新商品」の最後に1行足す
#   同じ商品ラベルが既にあれば足さない。重なりがあれば足さずに「要確認」で返す
# - 足した商品は毎週、当たった数だけを NEW PRODUCT へ返す。**商品名そのものは送らない**（数とラベルだけ）
# - 発売から1年で「有効」を外すことはしない（人が外す・十文字さんの決定 11-11）
# - weekly_report.ps1（9:00）より前の 8:30 に動かす。Excel が入っている PC で、TERASTATION が見えるユーザーで
# - 2026-11-19 追加（ADR-161）: 毎回、有効な全ラベルについて「ラベル×日の売上（金額だけ）」を年度の始まり（5/1）から数え、
#   NEW PRODUCT へ送る（/daily）。当て方は上と同じ。金額の列は -AmountCol（既定は下の候補の最初に見つかったもの）。
#   NEW PRODUCT が週次レポートの月別の表と月ごとの合計を比べるので、列の取り違えは画面に差として出る。-NoDaily で止められる
param(
    [string]$MasterPath = '\\TERASTATION\dashboard\sales_dashboard\weekly_report\新商品マスタ.xlsx',
    [string]$SalesCsv = '\\TERASTATION\dashboard\sales_dashboard\data\sales.csv',
    [string]$Url = 'https://newproduct.fun-create.co.jp/api/feed/newprod-master',
    [string]$TokenFile = (Join-Path $PSScriptRoot 'np_feed_token.txt'),
    [string]$AmountCol = '',
    [switch]$NoDaily,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$LogFile = Join-Path $PSScriptRoot 'sync_newprod_master.log'
function Log([string]$m) {
    $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $m
    Write-Output $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}
function Split-Kw([string]$s) {
    $a = @()
    if (-not [string]::IsNullOrWhiteSpace($s)) { foreach ($t in ($s -split ';')) { $t2 = $t.Trim(); if ($t2) { $a += $t2 } } }
    return ,$a
}
# weekly_report.ps1 と同じ当て方（; は全部を含む・(A|B) はどれか・除外は1つでも含めば外す・大文字小文字を区別）
function Test-Match([string]$name, [array]$inc, [array]$exc) {
    if ([string]::IsNullOrEmpty($name)) { return $false }
    foreach ($i in $inc) {
        if ($i -match '^\(([^)]+)\)$' -and $i.Contains('|')) {
            $any = $false
            foreach ($t in ($Matches[1] -split '\|')) { $t2 = $t.Trim(); if ($t2 -and $name.Contains($t2)) { $any = $true; break } }
            if (-not $any) { return $false }
        } elseif (-not $name.Contains($i)) { return $false }
    }
    foreach ($e in $exc) { if ($name.Contains($e)) { return $false } }
    return $true
}

try { $tok = (Get-Content -Path $TokenFile -Raw).Trim() } catch { Log ('鍵のファイルが読めません: ' + $TokenFile); exit 2 }
$h = @{ 'X-Feed-Token' = $tok }
try { $job = Invoke-RestMethod -Uri $Url -Headers $h -Method Get -TimeoutSec 60 } catch { Log ('NEW PRODUCT に届きません: ' + $_.Exception.Message); exit 3 }
$adds = @($job.add | Where-Object { $_ }); $checks = @($job.check | Where-Object { $_ })
if ($adds.Count -eq 0 -and $checks.Count -eq 0 -and $NoDaily) { Log 'やることはありません'; exit 0 }
Log ('足す ' + $adds.Count + ' 件・数え直す ' + $checks.Count + ' 件' + $(if ($NoDaily) { '' } else { '・ラベル×日の売上を送る' }))

# --- sales.csv（Shift-JIS）。商品名と売上日だけ使う ---
$enc = [System.Text.Encoding]::GetEncoding(932)
$rows = [System.IO.File]::ReadAllText($SalesCsv, $enc) | ConvertFrom-Csv
$props = $rows[0].PSObject.Properties.Name
$nameCol = @('商 品 名', '商　品　名', '商品名') | Where-Object { $props -contains $_ } | Select-Object -First 1
$dateCol = '売上日'
if (-not $nameCol -or -not ($props -contains $dateCol)) { Log 'sales.csv に商品名か売上日の列がありません'; exit 4 }

# --- 既存の行（有効なもの）を読む ---
$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false; $excel.DisplayAlerts = $false
$existing = @(); $labels = @{}
try {
    $wb = $excel.Workbooks.Open($MasterPath, 0, $true)
    $ws = $null; foreach ($sh in $wb.Sheets) { if ($sh.Name -eq '新商品') { $ws = $sh; break } }
    if ($null -eq $ws) { throw 'シート「新商品」がありません' }
    $data = $ws.UsedRange.Value2; $n = $ws.UsedRange.Rows.Count
    for ($r = 2; $r -le $n; $r++) {
        $lbl = [string]$data[$r, 1]
        if ([string]::IsNullOrWhiteSpace($lbl)) { continue }
        $labels[$lbl] = $true
        $act = $data[$r, 4]
        if (-not (($act -is [bool] -and $act) -or ([string]$act -match '^(TRUE|1|Yes|有効|オン)$'))) { continue }
        $existing += @{ Label = $lbl; Inc = (Split-Kw ([string]$data[$r, 2])); Exc = (Split-Kw ([string]$data[$r, 3])) }
    }
    $wb.Close($false)
} catch {
    Log ('新商品マスタが読めません: ' + $_.Exception.Message)
    $excel.Quit(); [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel); exit 5
}

# --- ラベル×日の売上（金額だけ・ADR-161）。商品名は送らない ---
$dailyExit = 0
if (-not $NoDaily) {
    $cands = @('売上(税込)', '売上金額(税込)', '税込金額', '売上金額', '金額', '小計')
    $amt = ''
    if ($AmountCol) { if ($props -contains $AmountCol) { $amt = $AmountCol } }
    else { $amt = $cands | Where-Object { $props -contains $_ } | Select-Object -First 1 }
    if (-not $amt) {
        Log ('ラベル×日: 金額の列が見つかりません。-AmountCol で列の名前を指定してください（sales.csv の列: ' + ($props -join '、') + '）')
        $dailyExit = 6
    } else {
        $now = Get-Date
        $fy = $(if ($now.Month -ge 5) { $now.Year } else { $now.Year - 1 })
        $dFrom = ('{0:D4}0501' -f $fy); $dTo = $now.ToString('yyyyMMdd')
        $inv = [System.Globalization.CultureInfo]::InvariantCulture
        $daily = @{}; $nRow = 0; $bad = 0
        foreach ($x in $rows) {
            $d = ([string]$x.$dateCol) -replace '[^0-9]', ''
            if ($d.Length -ne 8 -or $d -lt $dFrom -or $d -gt $dTo) { continue }
            $nm = [string]$x.$nameCol
            $hit = New-Object System.Collections.ArrayList
            foreach ($e in $existing) { if (Test-Match $nm $e.Inc $e.Exc) { [void]$hit.Add($e) } }
            if ($hit.Count -eq 0) { continue }
            $v = 0.0
            if (-not [double]::TryParse((([string]$x.$amt) -replace '[,\s円¥]', ''), [System.Globalization.NumberStyles]::Float, $inv, [ref]$v)) { $bad++; continue }
            $nRow++
            $key = $d.Substring(0, 4) + '-' + $d.Substring(4, 2) + '-' + $d.Substring(6, 2)
            foreach ($e in $hit) {
                if (-not $daily.ContainsKey($e.Label)) { $daily[$e.Label] = @{} }
                if ($daily[$e.Label].ContainsKey($key)) { $daily[$e.Label][$key] += $v } else { $daily[$e.Label][$key] = $v }
            }
        }
        Log ('ラベル×日: ' + $dFrom + '〜' + $dTo + '・金額の列「' + $amt + '」・当たった明細 ' + $nRow + ' 行・' + $daily.Count + ' ラベル' + $(if ($bad) { '・金額が読めない ' + $bad + ' 行（数えていません）' } else { '' }))
        if (-not $DryRun) {
            $dbody = ConvertTo-Json @{ from = $dFrom; to = $dTo; amount_col = $amt; labels = $daily } -Depth 5 -Compress
            try {
                $dr = Invoke-RestMethod -Uri ($Url + '/daily') -Method Post -Headers $h -TimeoutSec 120 `
                    -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($dbody))
                Log ('ラベル×日を送った: ' + $dr.rows + ' 行・週次レポートと比べて合わないのは ' + $dr.mismatch + ' か所（' + $dr.compared + ' か所を比べた）')
            } catch { Log ('ラベル×日を送れませんでした: ' + $_.Exception.Message); $dailyExit = 7 }
        }
    }
}
if ($adds.Count -eq 0 -and $checks.Count -eq 0) {
    $excel.Quit(); [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    [System.GC]::Collect(); [System.GC]::WaitForPendingFinalizers()
    Log '新商品マスタに足す行・数え直す行はありません'; exit $dailyExit
}

function Count-Row($it) {
    $inc = Split-Kw ([string]$it.include); $exc = Split-Kw ([string]$it.exclude)
    $from = ''; if ($it.release_date) { $from = ([string]$it.release_date) -replace '-', '' }
    $cnt = 0; $ov = @{}
    foreach ($x in $rows) {
        $d = [string]$x.$dateCol
        if ($from -and $d -lt $from) { continue }
        $nm = [string]$x.$nameCol
        if (-not (Test-Match $nm $inc $exc)) { continue }
        $cnt++
        foreach ($e in $existing) { if ($e.Label -ne $it.label -and (Test-Match $nm $e.Inc $e.Exc)) { $ov[$e.Label] = $true } }
    }
    return @{ Count = $cnt; Overlaps = @($ov.Keys) }
}

$results = @()
foreach ($it in $checks) {
    $c = Count-Row $it
    $results += @{ project_id = $it.project_id; result = '当たり'; matches = $c.Count; overlaps = @($c.Overlaps) }
    Log ('数え直し: ' + $it.label + ' → ' + $c.Count + ' 件')
}
$toWrite = @()
foreach ($it in $adds) {
    if ($labels.ContainsKey([string]$it.label)) {
        $results += @{ project_id = $it.project_id; result = '追加済み'; note = '同じ商品ラベルの行が既にありました（足していません）' }
        Log ('既にある: ' + $it.label); continue
    }
    $c = Count-Row $it
    if ($c.Overlaps.Count -gt 0) {
        $results += @{ project_id = $it.project_id; result = '要確認'; matches = $c.Count; overlaps = @($c.Overlaps);
                       note = '既存の行と同じ明細に当たります（二重に数えるので足していません）。除外キーワードで分けてください' }
        Log ('要確認（重なり）: ' + $it.label + ' ↔ ' + ($c.Overlaps -join '、')); continue
    }
    $toWrite += @{ It = $it; Count = $c.Count }
}

if ($toWrite.Count -gt 0 -and -not $DryRun) {
    try {
        $bk = Join-Path (Split-Path $MasterPath) ('新商品マスタ_backup_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.xlsx')
        Copy-Item -Path $MasterPath -Destination $bk
        $wb = $excel.Workbooks.Open($MasterPath, 0, $false)
        if ($wb.ReadOnly) { throw 'ほかの人が新商品マスタを開いています' }
        $ws = $null; foreach ($sh in $wb.Sheets) { if ($sh.Name -eq '新商品') { $ws = $sh; break } }
        $row = $ws.UsedRange.Row + $ws.UsedRange.Rows.Count
        foreach ($w in $toWrite) {
            $it = $w.It
            $ws.Cells.Item($row, 1).Value2 = [string]$it.label
            $ws.Cells.Item($row, 2).Value2 = [string]$it.include
            $ws.Cells.Item($row, 3).Value2 = [string]$it.exclude
            $ws.Cells.Item($row, 4).Value2 = $true
            if ($it.release_date) {
                $ws.Cells.Item($row, 5).Value2 = ([DateTime]::ParseExact([string]$it.release_date, 'yyyy-MM-dd', $null)).ToOADate()
                $ws.Cells.Item($row, 5).NumberFormat = 'yyyy/mm/dd'
            }
            for ($m = 0; $m -lt 12; $m++) {
                $ws.Cells.Item($row, 6 + $m).Value2 = [double]$it.monthly_sales[$m]
                $ws.Cells.Item($row, 18 + $m).Value2 = [double]$it.monthly_qty[$m]
            }
            $results += @{ project_id = $it.project_id; result = '追加済み'; matches = $w.Count }
            Log ('足した: ' + $it.label + '（' + $row + ' 行目・sales.csv で ' + $w.Count + ' 件）')
            $row++
        }
        $wb.Save(); $wb.Close($false)
        Log ('控え: ' + $bk)
    } catch {
        $msg = $_.Exception.Message
        foreach ($w in $toWrite) {
            if (-not ($results | Where-Object { $_.project_id -eq $w.It.project_id })) {
                $results += @{ project_id = $w.It.project_id; result = '失敗'; note = ('新商品マスタに書けませんでした: ' + $msg) }
            }
        }
        Log ('失敗: ' + $msg)
        try { $wb.Close($false) } catch {}
    }
} elseif ($DryRun) {
    foreach ($w in $toWrite) { Log ('足す予定: ' + $w.It.label + '（sales.csv で ' + $w.Count + ' 件）') }
}
$excel.Quit(); [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
[System.GC]::Collect(); [System.GC]::WaitForPendingFinalizers()

if ($DryRun) { Log '試す実行なので、NEW PRODUCT には返していません'; exit 0 }
if ($results.Count -gt 0) {
    $body = ConvertTo-Json @{ items = @($results) } -Depth 5
    try {
        $r = Invoke-RestMethod -Uri ($Url + '/result') -Method Post -Headers $h -TimeoutSec 60 `
            -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
        Log ('NEW PRODUCT に返した: ' + $r.applied + ' 件')
    } catch { Log ('NEW PRODUCT に返せませんでした（マスタは書いてあります）: ' + $_.Exception.Message); exit 1 }
}
exit $dailyExit
