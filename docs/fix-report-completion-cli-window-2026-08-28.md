# Skill Magnet 完了形式エラーとCLI残留 対応報告（2026-08-28）

## 変更

- `src/skill_magnet/activation.py`
  - `--ignore-user-config`を除去し、既存user configのMCP transport定義を保持したまま3 serverだけをprocess限定overrideで無効化。
  - runtime return code非0を `_RuntimeFailed` / `runtime_failed` に分離。
  - Windows runtime childへ`CREATE_NO_WINDOW`を指定。
- `src/skill_magnet/ui.py`
  - runtime起動後終了を日本語のfailed surfaceへ分離。schema/output mismatchを成功に変換しない。
- `src/skill_magnet/platforms.py`
  - Windows Explorer commandをinstalled `SkillMagnetLauncher.exe`経由へ変更。`pythonw.exe`には依存しない。
- `native/windows-modern-context-menu/SkillMagnetCommand.cpp`
  - Explorer→launcherを`CREATE_NO_WINDOW`で起動。
- `native/windows-modern-context-menu/SkillMagnetLauncher.cpp`
  - GUI-subsystem launcherがshellを介さず、埋込み済みmenu commandを`CREATE_NO_WINDOW`で起動するadapterになった。
- `native/windows-modern-context-menu/contract_test.py`
  - launcher childの`GetConsoleWindow()`が0であるnative smokeを追加。
- `tests/test_activation.py`
  - process-local overrideとuser config両立、Windows runtime creation flag、同じactual request fixtureの`verified_completed`、runtime/schema failure分離、launcher argvを回帰化。

## 安全境界

- global/user Codex config、Cloudflare/Unreal project、既存terminal processを変更・停止しない。
- errorをsuccessへ変換せず、`verified_completed`、actual-request hash、selected skill IDs、skill acceptance、cleanupのfail-closedを維持する。
- launcherはinstall済みmenu contractに記録されたargvをshellなしで起動する。`cmd /c start`は使わない。
- console抑止対象はSkill Magnetが生成するchildだけである。

## 検証結果

### 原因再現

Codex CLI 0.148.0へ旧argv前半を与え、`invalid transport in mcp_servers.cloudflare-builds`、exit 1を確認した。これはmodel呼出しを伴わないlocal config parseである。

### focused

最終実行結果:

```text
Ran 8 tests in 8.592s
OK

Ran 4 tests in 7.598s
OK

SkillMagnet IExplorerCommand contract PASS (Python host)
```

対象は成功、同一actual request、raw session非resume、failed/blocked表示、runtime failure、cancel、Windows launcher argv、unrelated cwd bootstrap。native build内contractと独立contractはPASSした。

### full / compile / diff

```text
Ran 104 tests in 101.184s
OK (skipped=1)

python -m compileall -q src tests
exit 0

git diff --check
exit 0（既存のLF→CRLF warningのみ）
```

## installed反映

install前はmodern packageが`usable_installed_state=true`、classic/legacy 4 root不在、active update不在だった。original rollbackは3 files / 7,970 bytes、tree hash `fad5c98933a12396d0f0e78fe14b43457e4aceceea59be53bd86aecf4edc7e30`だった。

単一update transactionを実行したが、modern package再登録はlocal package署名証明書のmachine trust昇格境界で失敗し、transactionは設計どおりmodernを除去してclassic fallbackを登録した。CLIはerror detail中のreplacement characterをcp932へ表示できず、transaction結果JSONの表示時に`UnicodeEncodeError`となったため、PowerShell subprocessのUTF-8 decodeを明示した。

classic fallbackもconsoleless launcherを使うため、modern失敗後にlauncherを保持する処理をsourceへ追加し、現installed fallbackへ同一build artifactを反映した。

| readback | 結果 |
|---|---|
| package | 未登録、modern unusable |
| visible roots | `SkillMagnetClassic` Directory / Backgroundのみ。legacy `SkillMagnet` 2 rootは不在 |
| installed launcher | `C:\Users\HOMEA\AppData\Local\SkillMagnet\ContextMenu\SkillMagnetLauncher.exe` |
| source / installed launcher SHA-256 | 双方 `4ce32aefccd574d8d04edad9d6f7bc23477141b9bdc9818fe640d259c5358fef` |
| command reference | live source `C:\Projects\skill-magnet\src`、config `C:\Projects\skill-magnet\skill-magnet.json` |
| active update | 不在 |
| original rollback | 上記hash、files、bytesすべて不変 |
| global Codex config SHA-256 | `ae98ac4db2b07b17511b92bdbc0beb1a0a9646293b0cb224e80af34c1afcff03`、前後不変 |

classic fallback commandは存在するlauncherを参照するため起動可能な構成だが、この時点では通常右クリックmodern rootではなかった。

### ユーザー承認後のrecovery

ユーザーが旧error dialogを閉じた後、PID `12552` / `7684`と同一commandの製品所有processが0であることを確認した。active update不在、original rollback不変を再確認してから、既存recovery transactionとしてmodern package再登録を1回実行した。package signing certificateのmachine trustにはWindowsの正式UAC promptを1回だけ使用し、自動承認やComputer Useは行っていない。

最終readback:

| 項目 | 結果 |
|---|---|
| modern package | `SkillMagnet.ContextMenu_1.0.0.0_x64__byy1sc3mfzfz4`、registered |
| modern usable | `true`。identity / COM identity / command target / DLL / menu manifest / Directory 2 contextすべて一致 |
| classic / legacy root | Directory / Backgroundの4 rootすべて不在 |
| DLL out / installed SHA-256 | 双方 `093ac1b27f585c6edfad80666fcf02a71c6b92ce85f0df4b464f7dce5171bf7c` |
| launcher out / installed SHA-256 | 双方 `e2d8e0c74f33097995a511b7f940e2ab3066111e1633e47d201c7bb73aec1733` |
| menu command | installed launcher、live source `C:\Projects\skill-magnet\src`、current configを参照 |
| active update | 不在 |
| original rollback | 3 files / 7,970 bytes / `fad5c98933a12396d0f0e78fe14b43457e4aceceea59be53bd86aecf4edc7e30`、不変 |
| global Codex config | `ae98ac4db2b07b17511b92bdbc0beb1a0a9646293b0cb224e80af34c1afcff03`、不変 |
| product-owned process | 0。旧PID `12552` / `7684`も不在 |

## 実機受入

Explorer/Tk実画面でconsoleが生成されないこと、および同一依頼が完了surfaceになることはComputer Use禁止のため未確認。実機受入前の状態はpartialである。

旧dialogを閉じた後のprocess残留0はreadback済み。新launcherのnative smokeもconsole handle 0を確認した。新しいExplorer実画面での右クリック起動と結果surfaceは未確認である。
