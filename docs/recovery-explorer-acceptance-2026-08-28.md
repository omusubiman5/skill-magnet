# Skill Magnet P3 Explorer 実機受入記録

作成日: 2026-08-28  
契約: `ORD-20260828-04`  
基準: `REC-P1-20260828` / P2 dirty manifest `576a6fca67767eb04fc8d9af4bd47e25b87735f3325a6c4956afa5d87672128b`  
状態: **ユーザーによる1回の手動受入待ち（未完了）**

## 1. Read-only preflight

- HEAD / branch: `084173e6e4bb117f3f420f0f2a5331a4c7018457` / `main`
- P2で固定した主要source/test 7ファイルのSHA-256は全件一致し、source driftなし。
- `C:\Users\HOMEA\.skill-magnet\pending-transaction.json` は存在しない。
- 実行前のstateにはlaunch contract 9件、verified evidence 5件、lifecycle 5件、rejected event 5件が存在する。最新の既存attemptは `059b85ce044c423c8cbf60deba08345d`（2026-08-27 22:22）であり、今回の受入とは区別する。
- Skill Magnet / `C:\Projects\skill-magnet` をcommand lineに持つ残存 `python` / `cmd` / `pwsh` / Windows Terminal processはない。照会自身の一時的な`pwsh`だけを観測し、停止操作はしていない。
- config: `C:\Projects\skill-magnet\skill-magnet.json`
- pack / expected commit: `codex-pmo-skills` / `c7747bba0bc391316aa558b3b4e8dd412045d2dc`
- 受入対象: `codex-auth-boundary-selection` / `Codex`
- 対象project: `C:\Projects\skill-magnet`

## 2. Menu transaction と rollback

新しいinstall transactionは開始していない。preflightで、以前中断されたupdate backup
`C:\Users\HOMEA\AppData\Local\SkillMagnet\ContextMenu.rollback.update` を検出したため、その同じbackupから復元処理を1回だけ実行した。

- package: `SkillMagnet.ContextMenu_1.0.0.0_x64__byy1sc3mfzfz4`、status `Ok`
- modern menuのpackage / COM identity / command target / DLL / manifestは一致し、Directory と Directory Background contextでusable。
- original rollback point `C:\Users\HOMEA\AppData\Local\SkillMagnet\ContextMenu.rollback` は保持した。
- 復元に使用済みのupdate backupは、active updateと誤認されないよう、内容を削除せず `C:\Users\HOMEA\AppData\Local\SkillMagnet\ContextMenu.rollback.recovered-20260828-000141` へ退避した。
- `ContextMenu.rollback.update` は現在存在しない。original rollback pointの上書き、複数install、Explorer自動操作、Computer Use、UAC代理操作は行っていない。

復元されたmenu manifestの代表leafは、current source `C:\Projects\skill-magnet\src`、上記config、expected commitとskill/instruction/acceptance digestをcommandへ固定している。

## 3. 1回の手動受入入力

ユーザーが入力するactual requestは次の完全一致文字列とする。

```text
認証境界の推奨を日本語で一文だけ作成してください。
```

UTF-8 SHA-256: `dc3c4763c75c4b0fb994136a484af29f02fcf523087865f6dd16dba80270e370`

## 4. 実行後に照合する証拠

ユーザー操作後に新規attemptを実行前ベースラインとの差分から一意に特定し、次を同一attemptとして照合する。

- launch contractのproject、pack、skill、runtime、commit、purposeとactual-request SHA-256
- completion evidenceの`completed_skill_ids`、`skill_execution_status=completed`、actual-request SHA-256、非空`task_output`、skill固有acceptance
- lifecycle terminal status `verified_completed`
- 対象source SHA-256がP2から不変であること
- 実行後process、temporary state、pending transaction、rollback状態

現時点では実機証拠がないため、P3を完了扱いしない。

## 5. Explorer leaf起動境界の修復

修復時刻: 2026-08-28 02:50:43 +09:00  
判定: **build / focused contract / single update install / readback PASS、手動受入待ち**

直近の手動操作ではmodern packageのCOM surrogateと`SkillMagnetCommand.dll`のloadまでは確認できたが、Python child、入力UI、launch contractは生成されなかった。成功比較対象のNEWS dashboardは`codex://threads/new`をOpenAI Codex packageのWindows protocol handlerへ渡す別経路であり、Skill Magnetの最初の未到達点はdynamic leafの`Invoke()`から`CreateProcessW`へ渡す境界だった。

### 最小修正

`native/windows-modern-context-menu/SkillMagnetCommand.cpp`で、`CreateProcessW`失敗直後の`GetLastError()`を退避し、既存のSkill Magnet error dialogへWindows error codeと`FormatMessageW`のsystem messageを表示するようにした。返却するHRESULTにも同じ退避済みcodeを使う。起動command、project quoting、config、pack、skill、runtime、commit、digestは変更していない。

- source SHA-256: `a75d006d9ebcda8d93ab6766a2b5243fb8d4875ffa12a7eb6294243d572cc310`
- diff: 1 file、21 insertions、3 deletions
- `git diff --check -- native/windows-modern-context-menu/SkillMagnetCommand.cpp`: whitespace errorなし。既存LF→CRLF warningのみ。

### Buildとfocused test

```text
pwsh.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass \
  -File native/windows-modern-context-menu/build.ps1 \
  -OutDir native/windows-modern-context-menu/out
```

結果: MSVC `/W4 /WX` build PASS。`SkillMagnet IExplorerCommand contract PASS`。既存LNK4104 warning 2件はexportのPRIVATE属性に関するwarningで、build failureではない。

```text
py -3.12 -m unittest -v \
  tests.test_activation.ActivationEndToEndTest.test_windows_modern_appx_registers_both_explorer_contexts \
  tests.test_activation.ActivationEndToEndTest.test_windows_modern_cli_files_and_package_status_are_reproducible \
  tests.test_activation.ActivationEndToEndTest.test_windows_product_install_skips_unsigned_development_contract_executable \
  tests.test_activation.ActivationEndToEndTest.test_windows_combined_install_is_repeatable_and_keeps_original_uninstall_point
```

結果: `Ran 4 tests in 4.015s`、`OK`。

install後に最新out DLLへnative contractを再実行し、再度`SkillMagnet IExplorerCommand contract PASS`、exit 0を確認した。

### 単一update transactionとreadback

既存の製品transaction APIを`--confirm`付きで1回だけ実行した。開始前はoriginal rollbackあり、active `.rollback.update`なし、package installed、signing certificateはCurrentUser/LocalMachine TrustedPeople双方に存在したため、UACは発生しなかった。

```text
python.exe -c <current src bootstrap> \
  --config C:\Projects\skill-magnet\skill-magnet.json \
  install-context-menu --platform windows --confirm
```

結果:

- package: `SkillMagnet.ContextMenu_1.0.0.0_x64__byy1sc3mfzfz4`、status `Ok`
- modern status: package / identity / COM identity / command target / DLL / menu manifest一致、`usable_installed_state=true`
- classic fallback: Directory / Directory Backgroundへ登録済み
- original rollback `C:\Users\HOMEA\AppData\Local\SkillMagnet\ContextMenu.rollback`: 保持
- active update `ContextMenu.rollback.update`: なし
- recovered evidence backup `ContextMenu.rollback.recovered-20260828-000141`: 保持
- transaction完了後、Python/cmd/Terminal childはなし。Explorerによるmenu再列挙で新buildをloadするSkill Magnet専用COM surrogate `dllhost.exe`は1件再生成されており、通常の待受状態として停止していない。

| artifact | current expected SHA-256 | installed SHA-256 | 判定 |
|---|---|---|---|
| `SkillMagnetCommand.dll` | `383390481f32b2182cca310716d0ab99dd17d78d55ad0c19ce8c7cbbf94db384` | `383390481f32b2182cca310716d0ab99dd17d78d55ad0c19ce8c7cbbf94db384` | match |
| `SkillMagnetLauncher.exe` | `10af6762e5b5df5711f596f488230204591bbec74dc7307c1066842313a28d37` | `10af6762e5b5df5711f596f488230204591bbec74dc7307c1066842313a28d37` | match |
| rendered `SkillMagnetMenu.tsv` | `ec17e4985923908857a7b690c5b9f0a15473ec2bbe27dcbd50492a53e4d67595` | `ec17e4985923908857a7b690c5b9f0a15473ec2bbe27dcbd50492a53e4d67595` | match |
| `AppxManifest.xml` | `4db2d9134b7aa26c20d3ba631cc6bb917eee0d101ad0b2fdb8b917687e035c3a` | `4db2d9134b7aa26c20d3ba631cc6bb917eee0d101ad0b2fdb8b917687e035c3a` | match |

実Explorer leafはまだ再選択していない。Computer Use、GUI自動操作、Codex/Claude起動は行っていないため、P3の最終判定は引き続きユーザーの1回の手動受入後に行う。

## 6. Skill Magnet確認UIの日本語化

既定表示を日本語にし、通常メニューのrootを `Skill Magnet`、leafを個別スキルの日本語表示名へ一本化した。主画面は選択スキル、用途、実行先AI、依頼内容だけを表示し、pack ID、repository、commit、全スキル一覧、digest、承認は既定で閉じた「詳細」へ移した。Codex/Claudeは画面上で明示選択し、選択値をcontractへ渡す。

actual requestが空白文字だけ、実行先AIが未選択、または個別スキルが未選択の場合は、contract作成や確認dialogへ進まずvalidationを表示する。Cancelとwindow closeはcontract、evidence、stateを作成しない。

この変更はnative menu contract、TSV、Python表示層、install transactionを変更するため、native buildと単一update installが必要である。modernがusableならclassic rootを削除し、modern不可時だけclassic fallbackを登録する。

検証結果:

- focused UI/既存routing/error: 5/5 PASS
- full suite: 100 tests PASS、1 skip
- `git diff --check`: whitespace errorなし（既存LF→CRLF warningのみ）
- Explorer、Computer Use、GUI自動操作、外部操作: 未実施

## 7. UX一本化 update transaction

作業契約: `ORD-20260828-07`  
判定: **実装・自動検証・単一update install・readback PASS、ユーザー手動受入待ち**

- modern menu contractをv3へ更新し、可視rootを `Skill Magnet`、leafを個別スキルの日本語表示名9件とした。leaf commandはpack、skill、commit、skill集合/instruction/acceptance digestを固定するがruntimeは固定せず、画面上でCodexまたはClaudeを明示選択する。
- modernがusableな場合はclassic/legacy 4 rootを削除し、modern unavailable時だけclassic fallbackを登録するtransaction testを追加した。
- native `/W4 /WX` buildと`SkillMagnet IExplorerCommand contract PASS`を確認した。focused 4件PASS、full suiteは100件PASS、1件skip、`git diff --check`はwarningのみでPASSした。
- `install-context-menu --platform windows --confirm` を1回実行し、modern `usable_installed_state=true`、classic `installed=false`、active `.rollback.update`なし、original rollback保持を確認した。
- classic/legacy registry root 4件はすべて未登録。source buildとinstalledのSHA-256はDLL `76355291f3918c79dca4bf3b6e8460de6f89c2459b3c7a64e84c27901ef7e1a8`、launcher `643f945b40100a65614a6815f8c6621c685a4a49f65bacab50611344a24e9d0c` で一致した。
- rendered/installed menu manifest SHA-256は `c5b53ea632844e8d713feace9d1d308a56e496f73cea2bda695c7fdbfa4b6a38` で一致した。
- Computer Use、Explorer自動操作、Codex/Claude実行は行っていない。通常右クリックの可視root、個別スキル表示、確認UI、actual request、選択AI、実行結果、`verified_completed` はユーザーの1回の手動受入後に判定する。それまではP3未完了である。
