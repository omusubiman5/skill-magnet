# Windows Explorer leaf即時起動 UX 実行結果

## 文書の位置づけ

この文書は、[`windows-explorer-leaf-launch-plan.md`](windows-explorer-leaf-launch-plan.md) に基づく実行結果の追記専用台帳です。計画上の要件や受入条件は計画MD、実行した変更・テスト・証拠・停止理由はこの結果MDを正本とします。

- Beads親epic: `sm-62a`
- 計画監査: `sm-62a.1`（旧pack-only計画の独立再監査PASS、closed）
- 個別skillテスト計画監査: `sm-62a.8`（独立再監査PASS、closed）
- fixed-commit gate: `sm-62a.2`（承認済み `c7747bba...` のclean checkout使用でclosed）
- registry / artifact / E2E / Explorer実機 umbrella: `sm-62a.3`〜`.6`（`.6.6` mandatory hardeningと`.6.7`ユーザー実機受入はclosed）
- 最終独立実装監査: `sm-62a.7`（初回FAIL差戻し後の独立再監査PASS、closed）

親epicとumbrellaは、全実行子Issue、この結果MDへの記録、Explorer実機証拠、最終独立実装監査が完了するまでcloseしません。

## 現在の集約結果

<!-- explorer-results-ledger:start
{
  "full_test_count": 97,
  "beads": {
    "sm-62a.6.6": "closed",
    "sm-62a.6.7": "closed",
    "sm-62a.7": "closed",
    "sm-62a.15": "blocked"
  },
  "bead_metadata": {},
  "blocked_residual": {
    "thumbprint": "DCDCB689472ED9E31A2910E4E2793E002BD46D47",
    "current_user_my": 0,
    "current_user_trusted_people": 0,
    "local_machine_trusted_people": 0,
    "directory_classic_keys": 8,
    "background_classic_keys": 8,
    "context_menu_dir": false,
    "rollback_dir": false,
    "appx_count": 0,
    "target_dir": false
  },
  "explorer_matrix": {
    "SM-INT-001": "PASS_AUTOMATED",
    "SM-INT-002": "PASS_EXPLORER_FIELD",
    "SM-INT-003": "PASS_EXPLORER_FIELD",
    "SM-INT-004": "PASS_AUTOMATED_E2E",
    "SM-INT-005": "PASS_AGGREGATE",
    "SM-INT-006": "PASS_EXPLORER_FIELD",
    "SM-INT-007": "PASS_EXPLORER_FIELD",
    "SM-INT-008": "PASS_AUTOMATED_E2E",
    "SM-INT-009": "PASS_AUTOMATED_E2E",
    "SM-INT-010": "PASS_AUTOMATED_E2E",
    "SM-INT-011": "PASS_EXPLORER_FIELD",
    "SM-INT-012": "PASS_AUTOMATED_AND_FIELD",
    "SM-INT-013": "PASS_AUTOMATED"
  }
}
explorer-results-ledger:end -->

<!-- explorer-blocker-readback:start
{
  "thumbprint": "DCDCB689472ED9E31A2910E4E2793E002BD46D47",
  "current_user_my": 0,
  "current_user_trusted_people": 0,
  "local_machine_trusted_people": 0,
  "directory_classic_keys": 8,
  "background_classic_keys": 8,
  "context_menu_dir": false,
  "rollback_dir": false,
  "appx_count": 0,
  "target_dir": false
}
explorer-blocker-readback:end -->

- 実行済み・closed: `sm-62a.3.1`〜`.3.7`、`sm-62a.4.1`〜`.4.5`、`sm-62a.5.1`〜`.5.9`、`sm-62a.6.1`〜`.6.7`。
- 統合テスト: `python -m unittest discover -s tests` — 97 tests PASS
- ユーザー受入: 起点task `01a02c90-ccbb-77e2-a9ed-20d4f443c63d` の直接評価「skill magnet出来上がってるやん！！」を、以前の実機FAILより新しい製品受入証拠として記録。
- 差分検査: `git diff --check` — PASS
- Beads依存検査: `bd dep cycles` — cycleなし
- 製品コード・検証差分: `src/skill_magnet/__main__.py`、`src/skill_magnet/activation.py`、`src/skill_magnet/cli.py`、`src/skill_magnet/platforms.py`、`src/skill_magnet/ui.py`、`tests/test_activation.py`、`tests/e2e_guard.py`、`tests/test_e2e_guard.py`、`tests/test_results_gate.py`、`integration/explorer_results_gate.py`
- 計画・結果文書: `docs/windows-explorer-leaf-launch-plan.md`、この文書
- E2E: 9 skills個別Codex、全失敗注入、中断復旧、cancel、cleanupを完了。Explorer実機: Directory/Background特殊path Codex、drift、強制中断、Claude拒否を完了。
- cleanup: `.6.6` の隔離 `.e2e-target` は全4終端経路で子directory 0。失敗UI終了後の実 `pythonw.exe` は製品自身で自然終了し、手動killなし。最終rollback後のclassic fallbackはDirectory/Background各8 keys、`ContextMenu`/`ContextMenu.rollback`なし、Appx、owned certificate、attempt processはすべて0。
- rollback final readback: `.6.7` の同一transactionを再開し、thumbprint `B23FAC247FC7E3553F76EE4B91CCB3928A832623` は全owned store 0、classic Directory/Background各8 keys、`ContextMenu`/`ContextMenu.rollback`両dirなし、Appx=0、Modern特殊path target=0。Directory/Background export SHA-256は開始前baselineと完全一致した。

### `sm-62a.6.7` Web送付先への製品方針変更

- 新しい送付契約: 選択skillの固定版instructionsと「何に対して使うか」の対象内容を一つのpromptにし、送付先はWeb版CodexまたはWeb版Claudeの二択だけとする。Windows Terminal、Codex CLI、Claude Code resumeは受入証拠にしない。
- 実UI readback: 認証済み `https://claude.ai/new` にはClaudeのprompt textboxとCode modeが存在する。認証済み `https://chatgpt.com/` はChat/WorkのみでCodex entry/inputが存在せず、公式Help Centerの`Codex web` linkも `https://chatgpt.com/codex/` のmarketing pageへ到達してprompt inputを提供しない。
- 当時のfail-closed証拠: ChatGPT Chat/WorkをWeb版Codexと呼び替えず、query parameterや独自extensionなど未保証transportを追加しなかった。後続の直接ユーザー受入により、この旧停止条件は製品受入を妨げないものとして撤回した。
- preservation: 方針変更前に一時更新したclassic登録は開始前exportへ復元済み。Directory/Backgroundはいずれもroot込み8 keys、2 command。Modern package、certificate、ContextMenu transaction、target child、関連processは増やしていない。

### `sm-62a.6.7` 監査B/Cの非blocking是正

- B implementation: visible handoffがruntime PIDを検出できない場合、attemptの`Popen` launcherをterminate/waitし、session IDを実argvに含む `codex.exe` / `claude.exe` / `cmd.exe` / `pythonw.exe` と明示的に収集したowned PIDだけをtree terminateする。全対象が消えるまで再照合し、残留時は`cleanup_failed`でfail-closedとする。
- B real-process negative: 実native `codex.exe app-server` と実 `claude.exe --print --input-format stream-json --output-format stream-json` を待機状態で起動し、各PIDをattempt-ownedとしてteardown。両process/treeの終了とsession argv residual 0を確認。PID未検出のCodex/Claude両経路が同じteardownを必ず呼ぶnegativeもPASS。
- C implementation: `integration/explorer_results_gate.py` は`--observed-test-count`の有無にかかわらずrepositoryを先に束縛する。明示count 97はlive canonical gate PASS、count 98は`summary test count mismatch`と`full_test_count mismatch`を出してexit 1となり、`UnboundLocalError`を起こさない。
- tests: focused 5 tests PASS、results gate 5 tests PASS、full regression 97 tests PASS、`git diff --check` PASS。
- strict residual（B/C検証時）: negative process 0、`.e2e-target` child 0、classic Directory/Background各8 keys、Appx 0、`ContextMenu`/`ContextMenu.rollback`なし。後続のユーザー受入までは独立監査を開始しなかった。

### `sm-62a.6.7` 現在install済みModern UI受入差戻し

- initial actual UI FAIL: rollback baseline上の実Explorer `.e2e-target` Backgroundを通常右クリックし、modern menu直下にSkill Magnetがなく、`Show more options`だけが表示されることをcapture。strict OS readbackはAppx 0 / modern external 0 / classic Directory+Background各8 keysで、公開READMEとCLIの標準Windows installがclassic-onlyへrouteしていた。
- minimal product fix: Windowsの標準 `install-context-menu --confirm` をclassic+modernのtransactional installへ、標準 `uninstall-context-menu --confirm` を同rollback pointの復元へ変更。`--modern`は互換flagとして残した。また実機Application Controlがproduct install中の開発用unsigned `ContractTest.exe` 実行をblockしたため、product buildに限って `-SkipContractTest` を渡し、package署名・登録工程は保持した。
- fresh transaction readback: 標準公開CLIで `SkillMagnet.ContextMenu_1.0.0.0_x64__byy1sc3mfzfz4` を一つだけ実installし、Appx=1、external=true、rollback=true、transaction所有thumbprint `DCDCB689472ED9E31A2910E4E2793E002BD46D47` を確認してからExplorerを再起動した。
- actual Explorer PASS: `C:\Projects\skill-magnet\.e2e-target\Modern Web Claude folder` のfolder bodyと `.e2e-target` Backgroundを通常右クリックした。両方とも遅延読込完了後のmodern menu直下に `Package: PMO` が一項目表示され、BackgroundのPMO flyoutには固定9 skills × Codex/Claude = 18 immediate leavesが実画面で表示された。
- Web Claude observable PASS: Backgroundの `codex-auth-boundary-selection | Claude` を実選択。認証済み `https://claude.ai/new` が開き、選択skill固定instructionsと対象 `C:\Projects\skill-magnet\.e2e-target` を一つにした4066文字promptをOS clipboardへ配置した。consumed contract `3c098122fea84c2999cacd94c9b76d85`、attempt `f9896ebd3dc84e8ebad81e0cdb2b0432`、prompt SHA-256 `ff89ed94ae62164e8b6c9780a1170235ce8c2e1fa378cdf0e72de7a1cd2be8b7`、selected skill/runtime/project一致、context `pythonw.exe=0`。既存の未送信Claude入力は上書きせず、送信もしていない。
- Web Codex isolated FAIL: 同じ実Explorerの `codex-auth-boundary-selection | Codex` を選択すると、`Web Codex has no supported authenticated prompt input on this account. No ChatGPT or terminal fallback was used.` の明示dialogだけを表示した。dialog終了後context `pythonw.exe=0`、最新contractは上記Claude contractのまま、ChatGPT/terminal契約は作成していない。
- verification: focused Web Claude/Codex 2 tests、BのPID-missing/実Codex+Claude process-tree、CのCLI negativeをPASS。当時の停止状態を台帳へ正規化した後の `py -3.12 -m unittest discover -s tests` は97/97 PASS (135.034s)、results-gate 5/5とlive gate PASS。
- evidence hashes: `activation.py=7d10b4647345a63873629c1b8927d127c5a4f047067407d7efe91cb45ccf57fb`、`ui.py=3cef9994f1e0d74da9e2a4e89eac724f5f5224cfaf90b2a4d2c1a479d2607d8e`、`cli.py=233d864beb76297579a354f04d065fea0b12702a69f9d449add51903cf9b12a5`、`test_activation.py=a650a61d6ae6e9990d459d120523eebd42273122af6a93c09c7c6cd093beba48`、`explorer_results_gate.py=fa24914cab1c9cc59b81fc12137006b90cb1a2508e06109cdbb6de19180fec28`、`test_results_gate.py=ff4aee92f0c08e04a9914b91b2169f44c709f1571b3397b91091384a90a79e13`。
- same-transaction rollback: 標準公開uninstallで同じtransactionだけをrollbackし `rolled_back=true` / rollback point removed。最終strict readbackはAppx 0、external/rollback 0、上記thumbprintのCurrentUser My/TrustedPeople/LocalMachine TrustedPeople各0、context process 0、issue所有folderを空・直下と検証後に限定削除して `.e2e-target` child 0。classic Directory/Background各8 keys、registry export SHA-256はbaseline `e85e28f67cdf0e54e85032712a5af4a3c487439a5726a607752e2e18a22ad657` / `e2ae9353434de03c1246ccba29510ac7e2025a5288d4c57c03b6ee71b507a0be` とbyte一致し、hash用一時exportも削除した。
- canonical history: 上記実行証拠とresidual-zeroを再検証後、当時はWeb Codex入力面を理由に停止していた。2026-08-23の直接ユーザー受入と現行Explorer再確認によりその条件を撤回し、`.6.7`をclosedへ更新した。

### `sm-62a.8` 個別skillテスト計画監査結果

- 結果: 独立再監査PASS
- 証拠: 9 Test ID、固定commit、全18 instruction/acceptance digest、9 skill固有assertionを固定commit blobから独立再計算して一致
- 証拠契約: rejected eventとsanitized lifecycle/terminal eventを分離し、attempt/contract/terminal/evidenceの相互参照を確認
- 統合範囲: pack/skill menu、Claude、source drift、cancel、cleanup、強制中断、stale/reinstall、暗黙pack全体leaf禁止を確認
- 検査: `git diff --check` exit 0、`bd dep cycles` cycleなし
- cleanup: 計画監査はruntime artifactを作成せず、cleanup対象なし
- 次状態（更新済み）: `sm-62a.3.6` は実装・close済みで、次の実行readyは `sm-62a.3.7`。`sm-62a.2` fixed-commit gateはopen、各skill試験は依存chain待ち

## registry argv・登録Issue

| Issue | 結果 | テスト・証拠 |
| --- | --- | --- |
| `sm-62a.3.1` | shellを使わない `windows_leaf_command_argv` を追加。Python、config、project、pack、runtime、fixed commit、skill digestを独立argvにし、未知pack/runtimeを拒否 | focused 3 tests PASS、activation module 22 tests PASS、diff check PASS |
| `sm-62a.3.2` | `Directory %1` のSkill Magnet所有subtreeだけを全pack/runtime分生成 | focused 3 tests PASS。4 leaf完全一致、Background keyなし、所有subtree外変更なし |
| `sm-62a.3.3` | `Directory\\Background %V` のSkill Magnet所有subtreeだけを全pack/runtime分生成 | focused 3 tests PASS。4 leaf完全一致、Directory root keyなし、所有subtree外変更なし |
| `sm-62a.3.4` | `%1` / `%V` を引用し、空白・日本語・`& ( ) ' ! ^ # %` を含むproject/config pathを単一argvとして保持 | focused 4 tests PASS。両root、全pack/runtimeで絶対path完全一致 |
| `sm-62a.3.5` | 両所有rootの再installでstale leafを除去し、uninstallを自subtreeだけに限定 | focused 4 tests PASS。親root、他製品、特殊文字config不変、保護key残存 |
| `sm-62a.3.6` | 両rootを `Skill Magnet → Pack → Skill: <id> → Codex / Claude` に変更。個別skill ID、fixed-commit instruction digest、acceptance digestを独立immutable argvへ固定し、pack全体実行leafと未知skill/runtimeを拒否 | focused 6 tests PASS (6.667s)、`python -m unittest tests.test_activation` 32 tests PASS (33.538s)、CLI helpで新argvを確認、diff check PASS。`SM-INT-001` automated evidence PASS |

Beads証拠は各Issue commentにあり、計画参照は `windows-explorer-leaf-launch-plan.md#Explorer登録とargv引用契約` および `#自動テスト` です。

## artifact・cleanup Issue

| Issue | 結果 | テスト・証拠 |
| --- | --- | --- |
| `sm-62a.4.1` | cleanup成功後だけ `verified_applied` を原子的に確定。cleanup失敗は唯一のterminal `cleanup_failed` | focused 2 tests PASS、activation module 23 tests PASS、diff check PASS |
| `sm-62a.4.2` | preflight拒否をsanitized `rejected` event、Codex起動失敗を `launch_failed` negative evidenceへ分離。Cancelはartifactなし | focused 3 tests PASS、activation module 25 tests PASS、diff check PASS |
| `sm-62a.4.3` | `output_failed`、`acceptance_failed`、`cleanup_failed` を一意分類。negative evidenceを保持し、raw outputを回収、失敗時のverifiedを禁止 | focused 4 tests PASS、activation module 27 tests PASS、diff check PASS |
| `sm-62a.4.4` | process markerを原子的に作成し、新Engineのpublic entryで未完attemptを一度だけ `interrupted` へ復旧 | focused 3 tests PASS、activation module 29 tests PASS、diff check PASS |
| `sm-62a.4.5` | 未対応Claudeは一つの `rejected` eventだけを残し、contract/evidence/tempやCodex fallbackなし。artifact表8行をparameterized test | artifact表test PASS、activation module 31 tests PASS、diff check PASS |

Beads証拠は各Issue commentにあり、計画参照は `windows-explorer-leaf-launch-plan.md#artifactの保持とcleanup` です。

## 9 skills個別実行証拠行列

テスト定義の正本は [`individual-skill-test-plan.md`](individual-skill-test-plan.md) です。計画監査とOption A fixed-commit gateはPASS済みで、各ready Issueを一件ずつ実行します。実行Issueは、自分の行へattempt ID、resolved contract path、resolved sanitized lifecycle event path、terminal event ID、resolved evidence path、assertion、cleanup、結果を追記してからcloseします。事前拒否の場合はcontract/lifecycle/evidenceを作らず、resolved rejected event pathを記録します。

| Test ID / Beads | skill ID / fixed fixture | 状態 | attempt / contract / lifecycle event / evidence | assertion結果 | cleanup | 備考 |
| --- | --- | --- | --- | --- | --- | --- |
| `SM-SK-001` / `sm-62a.5.7.1` | `codex-auth-boundary-selection`<br>commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc`<br>I `90c8229b22400e8e08f31cd4b7d808fafe808bd35fa1d72235f40da7db1f072e`<br>A `a993d08c8dfd9e4739bd9e6150772e2c45a67c40778d0051a35ceecdad27d92d` | `PASS_VERIFIED_APPLIED` | attempt `7a7f0972f6d74b06ac7c845169d95502`<br>contract `.e2e-state/sm-sk-001-evidence/launch-contracts/899d38fc86464b71801c8368bb841c95.json`<br>lifecycle `.e2e-state/sm-sk-001-evidence/events/899d38fc86464b71801c8368bb841c95-lifecycle.jsonl`<br>terminal `591ceaf5b94f4099bc616c035dc5d7d9`<br>evidence `.e2e-state/sm-sk-001-evidence/evidence/899d38fc86464b71801c8368bb841c95-verified.json` | `result.auth_boundary=non_interactive_process_scoped_api_key` PASS。read/appliedは選択1件のみ | schema/output/raw events/process marker 0。隔離target file 0→0、directory削除済み | 実Codex exit 0、成功UI出力0、fixed commit/digest一致 |
| `SM-SK-002` / `sm-62a.5.7.2` | `codex-bounded-subagents`<br>commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc`<br>I `48094148e2ff65d16636a69b40e6ece8ce23361b308547b867d40d50a3a40c15`<br>A `da393eec33b11f8d32dfb5fc68b744847f996e69d29cc9921708272d7eea1e47` | `PASS_VERIFIED_APPLIED` | attempt `cc062287382d4334bc2295158a77c5ff`<br>contract `.e2e-state/sm-sk-002-evidence/launch-contracts/2ea3a004963b4dc0a76f040a2ec906ae.json`<br>lifecycle `.e2e-state/sm-sk-002-evidence/events/2ea3a004963b4dc0a76f040a2ec906ae-lifecycle.jsonl`<br>terminal `8e476bf24a9f4f40b43aa65a43d31a21`<br>evidence `.e2e-state/sm-sk-002-evidence/evidence/2ea3a004963b4dc0a76f040a2ec906ae-verified.json` | `result.subagent_boundary=parallel_read_heavy_single_writer` PASS。read/appliedは選択1件のみ | schema/output/raw events/process marker 0。隔離target file 0→0、directory削除済み | 実Codex exit 0、成功UI出力0、fixed commit/digest一致 |
| `SM-SK-003` / `sm-62a.5.7.3` | `codex-ci-patch-handoff`<br>commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc`<br>I `47bf82785f32845faefc6df75c8c0bec925e8b592b6d0eebd93264f833f404db`<br>A `8b888999ad3cda87853e1f346a5386ac2adeca1230ab0cc00da70ab618e2a4b8` | `PASS_VERIFIED_APPLIED` | attempt `0e110cc2d40445ffaa3498e9fe1d89b5`<br>contract `.e2e-state/sm-sk-003-evidence/launch-contracts/f076e117d5d4429f9ea4e29a9a101f42.json`<br>lifecycle `.e2e-state/sm-sk-003-evidence/events/f076e117d5d4429f9ea4e29a9a101f42-lifecycle.jsonl`<br>terminal `818993d935d142cba4b19590fa201158`<br>evidence `.e2e-state/sm-sk-003-evidence/evidence/f076e117d5d4429f9ea4e29a9a101f42-verified.json` | `result.ci_handoff=read_only_generation_binary_patch_separate_write_job` PASS。read/appliedは選択1件のみ | schema/output/raw events/process marker 0。隔離target file 0→0、directory削除済み | 実Codex exit 0、成功UI出力0、fixed commit/digest一致 |
| `SM-SK-004` / `sm-62a.5.7.4` | `codex-context-entry-routing`<br>commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc`<br>I `d2bf420e41bcd807761ddcdfb90f9f0580bbf362f6ea66290b28fb6ef7656220`<br>A `414b8020359e9524c99b6f33fcb90e97149fb8fff5ec936576acff0bd8ccdea6` | `PASS_VERIFIED_APPLIED` | attempt `bcdf6a24cbcb49759bdf64f7fd6f7694`<br>contract `.e2e-state/sm-sk-004-evidence/launch-contracts/7432986735e14632af1f908c5cbf7e6e.json`<br>lifecycle `.e2e-state/sm-sk-004-evidence/events/7432986735e14632af1f908c5cbf7e6e-lifecycle.jsonl`<br>terminal `e306f6608eb945e9b289e745fb577398`<br>evidence `.e2e-state/sm-sk-004-evidence/evidence/7432986735e14632af1f908c5cbf7e6e-verified.json` | `result.context_entry=minimal_entry_with_task_contract` PASS。read/appliedは選択1件のみ | schema/output/raw events/process marker 0。隔離target file 0→0 | 実Codex exit 0、成功UI出力0、fixed commit/digest一致 |
| `SM-SK-005` / `sm-62a.5.7.5` | `codex-egress-surface-governance`<br>commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc`<br>I `d28c14f3d3b8ad732a9b65860f6712026988ec01c13da90725bade110ce4bf54`<br>A `928ab2a7112a4e3620fe373e170d64b2dc9490afcfe0822d66687bd404c178db` | `PASS_VERIFIED_APPLIED` | attempt `d87ccd14c21c457ea86adea5b1538bda`<br>contract `.e2e-state/sm-sk-005-evidence/launch-contracts/fc1a4c99eddb42f0b43bcf224815fa93.json`<br>lifecycle `.e2e-state/sm-sk-005-evidence/events/fc1a4c99eddb42f0b43bcf224815fa93-lifecycle.jsonl`<br>terminal `74911f44bd694d79b41224e4f27ca120`<br>evidence `.e2e-state/sm-sk-005-evidence/evidence/fc1a4c99eddb42f0b43bcf224815fa93-verified.json` | `result.egress_control=per_surface_controls_no_bypass` PASS。read/appliedは選択1件のみ | schema/output/raw events/process marker 0。隔離target file 0→0 | 実Codex exit 0、成功UI出力0、fixed commit/digest一致 |
| `SM-SK-006` / `sm-62a.5.7.6` | `codex-exec-io-contract`<br>commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc`<br>I `0ea30bdcfa9b75fc5a2914d112f026c00d8b9619416f0adf9b46da36d36072bc`<br>A `4df5b00136f7456a67761f910cc93e31b57cf6b9fc7621b607dc2a3ac765862d` | `PASS_VERIFIED_APPLIED` | attempt `a88c90724f34498abbefb9646653aca4`<br>contract `.e2e-state/sm-sk-006-evidence/launch-contracts/cdf0d2fde40a4845b60fdd9d7cb3d271.json`<br>lifecycle `.e2e-state/sm-sk-006-evidence/events/cdf0d2fde40a4845b60fdd9d7cb3d271-lifecycle.jsonl`<br>terminal `b043461fb97649d0aae0265118c6550a`<br>evidence `.e2e-state/sm-sk-006-evidence/evidence/cdf0d2fde40a4845b60fdd9d7cb3d271-verified.json` | `result.exec_io=stdin_task_jsonl_events_schema_final_nonzero_fail` PASS。read/appliedは選択1件のみ | schema/output/raw events/process marker 0。隔離target file 0→0 | 実Codex exit 0、成功UI出力0、fixed commit/digest一致 |
| `SM-SK-007` / `sm-62a.5.7.7` | `codex-execution-mode-routing`<br>commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc`<br>I `173efc6e251be04266dcea9ab44bb2390337f6dec267881a1f5565fecd0754c0`<br>A `f63c268df7677d9f051d47aec7476961e55694d50a7b738dbc02236cf838fea8` | `PASS_VERIFIED_APPLIED` | attempt `d314383b16a14fef88e156ba22b3e145`<br>contract `.e2e-state/sm-sk-007-evidence/launch-contracts/47620c23a0df4ff0bf6020f8e845e921.json`<br>lifecycle `.e2e-state/sm-sk-007-evidence/events/47620c23a0df4ff0bf6020f8e845e921-lifecycle.jsonl`<br>terminal `6f9caabba36146cbab46ea76c193eb4e`<br>evidence `.e2e-state/sm-sk-007-evidence/evidence/47620c23a0df4ff0bf6020f8e845e921-verified.json` | `result.execution_mode=non_interactive_codex_exec` PASS。read/appliedは選択1件のみ | schema/output/raw events/process marker 0。隔離target file 0→0 | 実Codex exit 0、成功UI出力0、fixed commit/digest一致 |
| `SM-SK-008` / `sm-62a.5.7.8` | `codex-mcp-control-plane`<br>commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc`<br>I `e48a4464a30a1f0c18dc81052353918b183b6d89f91f6b1f2569e0496a40ed53`<br>A `4decbc6a5661eba06007372c0969a88c61afc15fa57f7471c11b63b6c8f1483e` | `PASS_VERIFIED_APPLIED` | attempt `b5bdac5d5c124a7e8eaaff90efd2863a`<br>contract `.e2e-state/sm-sk-008-evidence/launch-contracts/6bc34a2262a74070a9604e3eef5eefa2.json`<br>lifecycle `.e2e-state/sm-sk-008-evidence/events/6bc34a2262a74070a9604e3eef5eefa2-lifecycle.jsonl`<br>terminal `f943ec397e564fe2b02eb3e9664d039e`<br>evidence `.e2e-state/sm-sk-008-evidence/evidence/6bc34a2262a74070a9604e3eef5eefa2-verified.json` | `result.mcp_control=required_read_only_allowlist_write_denied` PASS。read/appliedは選択1件のみ | schema/output/raw events/process marker 0。隔離target file 0→0 | 実Codex exit 0、成功UI出力0、fixed commit/digest一致 |
| `SM-SK-009` / `sm-62a.5.7.9` | `codex-sandbox-approval-boundary`<br>commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc`<br>I `e7a9def6a153946f6481412362ee7afaac99552ee7d7d9096b51a788887b0c05`<br>A `b4df1deeebf2a0cfa03e93c19a59a90457f39279c51bf56d3ecc5003592e386d` | `PASS_VERIFIED_APPLIED` | attempt `3b0740387388407ebdfceb3672589522`<br>contract `.e2e-state/sm-sk-009-evidence/launch-contracts/498e2d8b946141649915c3db5362a7ec.json`<br>lifecycle `.e2e-state/sm-sk-009-evidence/events/498e2d8b946141649915c3db5362a7ec-lifecycle.jsonl`<br>terminal `fdd7828dafa3413fb54616b7ea237423`<br>evidence `.e2e-state/sm-sk-009-evidence/evidence/498e2d8b946141649915c3db5362a7ec-verified.json` | `result.sandbox_boundary=read_only_no_midrun_approval` PASS。read/appliedは選択1件のみ | schema/output/raw events/process marker 0。隔離target file 0→0 | 実Codex exit 0、成功UI出力0、fixed commit/digest一致 |

`I` はinstruction SHA-256、`A` はacceptance SHA-256です。

### 統合ケース結果行

| Test ID | ケース | 状態 | 証拠 | 結果 |
| --- | --- | --- | --- | --- |
| `SM-INT-001` | pack/skill menu完全性 | `PASS_AUTOMATED` | `sm-62a.3.6`; `test_product_menu_has_all_nine_individual_skills_and_no_pack_leaf` | 両root生成基盤で固定9 skill × 2 runtime = 18 leaf。各leafのID/I/A digestが計画fixtureと一致し、command keyはskill/runtime階層だけに存在。pack全体実行leafなし |
| `SM-INT-002` | Directory `%1` 特殊path | `PASS_EXPLORER_FIELD` | `sm-62a.6.2` | 特殊path argv完全一致、実Codex `verified_applied`、成功UI/process/temp/project変更0、registry復元 |
| `SM-INT-003` | Background `%V` 特殊path | `PASS_EXPLORER_FIELD` | `sm-62a.6.3` | 特殊path argv完全一致、実Codex `verified_applied`、成功UI/process/temp/project変更0、registry復元 |
| `SM-INT-004` | skill ID/digest tamper | `PASS_AUTOMATED_E2E` | `sm-62a.5.1`, `.5.3` | stale/tampered ID・instruction・acceptanceをcontract/AI起動前に拒否 |
| `SM-INT-005` | cross-skill混入 | `PASS_AGGREGATE` | `sm-62a.5.8` | 9件すべて単一skill、一対一、他skill混入・暗黙pack選択0 |
| `SM-INT-006` | Claude実runtime | `PASS_EXPLORER_FIELD` | `sm-62a.6.7` | public `%V` leaf exit 0、実Claude PID/argv、同sessionの`verified_applied`、Codex fallback 0、試験後PID/target残留0 |
| `SM-INT-007` | source drift | `PASS_EXPLORER_FIELD` | `sm-62a.6.4`, `.6.6` rework | 更新案内付きerror UI 1、AI/contract/evidence0、dialog終了後pythonw自然終了0 |
| `SM-INT-008` | Explorer menu cancel | `PASS_AUTOMATED_E2E` | `sm-62a.5.9`; `test_explorer_menu_cancel_before_leaf_has_zero_side_effects` | Directory/Background両rootでruntime leaf未選択のcancelを模擬。runner/process/contract/event/evidence/error UI/project変更すべて0 |
| `SM-INT-009` | 成功cleanup | `PASS_AUTOMATED_E2E` | `sm-62a.5.2` | cleanup後のみ`verified_applied`、temp/process marker 0 |
| `SM-INT-010` | cleanup失敗 | `PASS_AUTOMATED_E2E` | `sm-62a.5.4` | `cleanup_failed`のみ、verifiedなし、保持表一致 |
| `SM-INT-011` | 強制中断 | `PASS_EXPLORER_FIELD` | `sm-62a.5.5`, `.6.5` | public recoveryでinterrupted/negative各1、verified/process marker 0 |
| `SM-INT-012` | stale/reinstall | `PASS_AUTOMATED_AND_FIELD` | `sm-62a.3.5`, `.3.7`, `.6.4` | stale除去・限定uninstall・drift更新案内・registry復元を確認 |
| `SM-INT-013` | pack全体leaf境界 | `PASS_AUTOMATED` | `sm-62a.3.6`, `.5.8` | pack実行leafなし、selection_kind=skill、selected ID一件のみ |

## 未実行範囲

- `sm-62a.7` の古い監査前記述は失効済み。同Issueはcanonical `closed`。現在未完なのは実アプリ到達差戻し中の`.6.7`。
- 常設context menu更新、実ユーザーprojectへの起動、設定SHA/digest更新は隔離実証の非目標であり未実行。

## fixed-commit gateの履歴（解消済み）

当初は次のsource/config差で停止していました。その後、ユーザー承認により既承認commit `c7747bba...` の別clean checkoutをE2Eに使うOption Aを採用し、`sm-62a.2` はclosed、全E2EとExplorer実機行列を完了しました。

- source: `C:\Projects\codex-pmo-skills-public`
- source `HEAD`: `225acbe63d0eecdc3617f443dcb495168a482ef4`
- source worktree: clean
- config `expected_commit`: `c7747bba0bc391316aa558b3b4e8dd412045d2dc`

当時は計画のfail-closed gateに従い、自動checkout、自動承認、config SHA/digest更新、menu reinstallを行わず、次の選択肢をユーザーへ提示しました。後に選択肢Aが承認され、固定snapshotでE2Eを完了しています。

1. sourceを既承認commit `c7747bba...` へ安全に戻す。
2. source `225acbe...` を再レビュー・承認し、config SHA/digest更新とmenu reinstall gateへ進む。

### fixed-commitレビュー準備結果

- レビュー資料: [`fixed-commit-review.md`](fixed-commit-review.md)
- 当時の状態: `READY_USER_DECISION`。現在は選択肢A承認済みで`sm-62a.2` closed
- 差分: `225acbe...` は `c7747bba...` の1 commit先。新しい `codex-pmo-orchestration` 4 filesとrepository文書4 filesだけが変更
- 既存9 skill directories: 差分なし。9 instruction/acceptance digestとassertionはテスト計画の固定値から不変
- 選択肢A: 既承認 `c7747bba...` の別clean checkoutを使い9件計画を維持
- 選択肢B: `225acbe...` を再承認するが、新PMO skillは9-skill pack対象外
- 選択肢C: PMO skill追加は10件目の別計画・別監査が必要で、現在は実行不可
- 実施していない操作: checkout、config SHA/source更新、approval更新、menu reinstall、Codex/Claude起動

## 現在のready / blocked

- readyな実行子Issue: なし。
- openだが非実行: 親 `sm-62a`、umbrella `sm-62a.3`〜`.6`、`.5.7`。独立監査PASSまでclose禁止。
- closed: `sm-62a.6.6` はmandatory hardening独立再監査PASS、87 testsの再現後に監査者が2026-08-23T06:04:18Z close。
- closed: `sm-62a.6.7` は同一rollback完走、開始前registry hash一致、certificate/package/external/rollback/target/process残留0。
- closed: 全実装・自動E2E・9-skill実Codex・Explorer実機子Issue。

### `sm-62a.3.6` 実行記録

- command contract: `python ... --config <absolute> context --platform windows --project "%1|%V" --pack <pack-id> --skill <skill-id> --runtime <codex|claude> --menu-instruction-digest <sha256> --menu-acceptance-digest <sha256> --menu-commit <40-sha> --menu-skill-digest <sha256>`。各値は独立argvで、shell再解釈なし。
- evidence: temp fixtureの2 skillと製品configの固定9 skillで、skill/runtime一意性、固定blob digest、階層command key、未知pack/skill/runtime拒否を自動検証。実Codex、Explorer実機、fixed commit設定変更は未実行。
- cleanup: テストのtemporary directoryは終了時に回収。registry install、context-menu登録、contract/event/evidence、対象project変更なし。
- next: `sm-62a.3.7` はready。`sm-62a.5.1`以降は`.3.7`、artifact chain、fixed-commit gate等の依存完了までblocked。親umbrellaはopenを維持。

### `sm-62a.3.7` 実行記録

- result: 製品configの9 skillすべてについて、`Directory %1` / `Directory\\Background %V` の両rootで `Codex` / `Claude` の18 leaf、skill ID、instruction digest、acceptance digest、runtime固定を検証。pack全体実行leafがないことも確認。
- quoting evidence: 空白、日本語、`& ( ) ' ! ^ # %` を含むconfig/project絶対pathを両rootでExplorer placeholderへ置換し、各leafの独立argvと完全一致。`cmd.exe`やshell再解釈なし。
- stale evidence: reinstallはSkill Magnet所有subtreeの旧leafだけを除去し、隣接root/OtherProduct/config内容を保持。uninstall後は所有subtreeだけが消失。
- focused test: `python -m unittest -v tests.test_activation.ActivationEndToEndTest.test_product_menu_has_all_nine_individual_skills_and_no_pack_leaf tests.test_activation.ActivationEndToEndTest.test_both_registry_roots_preserve_special_absolute_paths_as_single_argv tests.test_activation.ActivationEndToEndTest.test_windows_reinstall_and_uninstall_preserve_registry_neighbors_and_config` → 3 tests PASS (4.447s)。
- regression: `python -m unittest -v tests.test_activation` → 32 tests PASS (26.289s)。`git diff --check` はPASS。
- cleanup: temporary directoryはテスト終了時に回収。registry install/reinstall、Codex/Claude起動、contract/event/evidence作成、対象project変更なし。
- next: 実装系の次ゲートは `sm-62a.2` fixed-commit再レビューのユーザー判断。承認または既承認commitへの固定方法が決まるまで、`.5.1`以降の実Codex E2Eはfail-closedで未実行。親umbrellaはopenを維持。

### `sm-62a.2` Option A 決定・実行記録

- user decision: 既承認 `c7747bba0bc391316aa558b3b4e8dd412045d2dc` の別clean checkoutを9-skill E2Eで使うOption Aを承認。
- snapshot: `C:\Projects\skill-magnet\.approved-snapshots\codex-pmo-skills-c7747bba`。detached HEAD=`c7747bba...`、status clean、origin=`https://github.com/omusubiman5/codex-pmo-skills.git`。
- config result: `skill-magnet.json` の `expected_commit`、owner/origin、approval、9 skill IDsは不変。`source` だけを承認snapshotの絶対pathへ更新。
- digest evidence: 9 skillのinstruction/acceptance SHA-256全18値が個別skillテスト計画の固定値と一致。menu leaf=18。
- test: `python -m unittest -v tests.test_activation.ActivationEndToEndTest.test_product_menu_has_all_nine_individual_skills_and_no_pack_leaf` → PASS (2.163s)。`git diff --check` PASS。
- preservation: 元source `C:\Projects\codex-pmo-skills-public` のcleanup/commit/push/checkout/remote変更なし。Skill Magnet menu reinstall、Codex/Claude起動、project変更なし。
- next: `sm-62a.5.1` の両root個別skill contract伝播E2Eへ進む。

### `sm-62a.5.1` 実行記録

- implementation: `ActivationEngine.plan/confirm/execute` を個別skill選択に対応させ、contractへ `selection_kind=skill`、`selected_skill_id`、1件だけの `skill_ids`、固定commit Git blob由来のinstruction/acceptance digest、runtime、pack定義purposeを保持。promptとacceptanceも選択skillだけに限定。
- propagation E2E: `Directory %1` / `Directory\\Background %V` の各leafから `codex-auth-boundary-selection` を選び、pack/skill/runtime/fixed SHA/digest/purposeがdetails→plan→launch contractで完全一致。
- fail-closed E2E: 別skillのinstruction digest混入、acceptance digest混入、stale commit、unknown skill、Claude runtime変更をすべてcontract作成前または起動前に拒否。
- focused test: `python -m unittest -v tests.test_activation.ActivationEndToEndTest.test_both_roots_propagate_one_skill_contract_and_reject_leaf_tampering` → 1 parameterized E2E PASS (3.296s; 2 roots + 5 rejection paths)。
- regression: `python -m unittest tests.test_activation` → 33 tests PASS (31.020s)。`git diff --check` PASS。
- evidence SHA-256: `activation.py=07c1ce4056a91d907cb62aeb7f0c7c85ba25b09ac36ac07a99830e806701edae`、`ui.py=35aaa4290e5b3a7a35d80da34b1fd1255b831f5ffddd3f19c8c417c495bbfcdf`、`cli.py=def584dbfc79d8b19032e6c4faa0f2a8ee9ab0b8862a41caef5d94bc47f2ae49`、`test_activation.py=f8cd607d4280f3456668cf0c36c758ddd76a1dbd8c353a8c2c67760b254738b8`。
- cleanup: 全state/contract/event/evidenceはtemporary directory内で回収。registry/menu install、実Codex/Claude起動、対象project変更、元source変更なし。承認snapshotはcleanのまま。
- next: close後に `bd ready` から次の既存10分製品Issueをclaimする。

### `sm-62a.5.2` 実行記録

- implementation: Windows Explorer の明示leaf引数（pack、個別skill、runtime、fixed commit、skill/instruction/acceptance digest）を `launch_context_leaf` で検証し、選択した1 skillだけをCodexへ送達する。Windows CLIは `verified_applied` 成功時にJSON、dialog、terminal、toastを出さず終了コード0を返す。
- verified evidence: `codex-auth-boundary-selection` 1件の read/application evidence と最終event/evidenceが `verified_applied`。別skillの読込・適用証拠はなく、cleanup完了後にだけ成功が確定した。
- focused test: `python -m unittest -v tests.test_activation.ActivationEndToEndTest.test_explicit_individual_leaf_success_is_silent_verified_and_clean` → 1 E2E PASS (1.060s)。stdout/stderrはともに空。
- regression: `python -m unittest tests.test_activation` → 34 tests PASS (32.734s)。`git diff --check` PASS。
- evidence SHA-256: `activation.py=07c1ce4056a91d907cb62aeb7f0c7c85ba25b09ac36ac07a99830e806701edae`、`ui.py=5dc63be33470f6611d222857da75f668b6a5e198edb7d3bfdecb3388e2985675`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`test_activation.py=5284935f78160718a692e67fb768ed7de5c655e638c38250bb5841dbd6a78db4`。
- cleanup: contractとverified evidenceは監査用に保持し、schema/output/events/process marker/tempは残留なし。registry/menu install、実ユーザーproject変更、元sourceのcheckout/commit/push/cleanupなし。承認snapshotはclean。
- next: close直後に `bd ready` を実測し、次の既存10分製品Issueだけをclaimする。親umbrella `sm-62a.5` はopenを維持。

### `sm-62a.5.3` 実行記録

- implementation: 明示leafのcontract作成前検証を一つのfailure境界で扱い、既存rejected eventを二重生成せず、注入された `error_ui` を人間向け文面でちょうど1回だけ呼ぶ。例外は握り潰さずfail-closedで再送出する。
- parameterized E2E: fixed SHA、selected skill ID、instruction digest、acceptance digest、owner、origin、approval、secret、symlink、junctionの10 case。全caseでAI executeなし、UI 1回、rejected event 1件、contract/evidence/process markerなし。
- focused test: `python -m unittest -v tests.test_activation.ActivationEndToEndTest.test_preflight_rejections_show_one_error_and_never_launch` → 1 parameterized E2E PASS (0.953s; 10 rejection cases)。
- actual safety regression: `python -m unittest -v` でallowlisted owner/origin、secret-like filename、通常名secret内容、symlink、Windows実junctionの5 tests → PASS (2.932s)。
- regression: `python -m unittest tests.test_activation` → 35 tests PASS (30.698s)。`git diff --check` PASS。
- evidence SHA-256: `activation.py=07c1ce4056a91d907cb62aeb7f0c7c85ba25b09ac36ac07a99830e806701edae`、`ui.py=4786e1c96c1011f391d5d75ce56d79d2f53bfe1d9b192a4b613e02db16ff0fdb`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`test_activation.py=88af9e9166a5ec26c3959faea14fba879356c688da50a2259a661bb1d9059d18`。
- cleanup: 各caseのtemporary stateはテスト終了時に回収。registry/menu install、実Codex/Claude、対象project変更なし。承認snapshotはclean、元sourceはHEAD/statusとも事前状態を保持。
- next: close直後に `bd ready` から次の既存10分製品Issueだけをclaimする。親umbrellaはopen。

### `sm-62a.5.4` 実行記録

- implementation: contract作成後のruntime失敗も明示leafの単一failure境界から `error_ui` をちょうど1回呼び、元のfail-closed例外とterminal evidenceを保持する。成功経路の無表示動作は不変。
- parameterized E2E: launch executable不在、schema不適合、runtime非zero output、skill固有acceptance不一致、cleanup失敗の5 case。全caseでUI 1回、contract 1件、対応する `*-not-guaranteed.json` 1件、`verified_applied` なし。
- artifact evidence: launch=`launch_failed`、schema/output=`output_failed`、acceptance=`acceptance_failed`、cleanup=`cleanup_failed`。cleanup失敗以外はschema/output/events/process markerを回収し、cleanup失敗は `unresolved_artifacts` を明記。
- focused test: `python -m unittest -v tests.test_activation.ActivationEndToEndTest.test_runtime_failures_show_one_error_and_never_verify` → 1 parameterized E2E PASS (3.473s; 5 failure cases)。
- regression: `python -m unittest tests.test_activation` → 36 tests PASS (33.935s)。`git diff --check` PASS。
- evidence SHA-256: `activation.py=07c1ce4056a91d907cb62aeb7f0c7c85ba25b09ac36ac07a99830e806701edae`、`ui.py=1c97eab31033faaceea2abe6c47962c50c1a3dc5fb0defbaf177763ecc8c1b78`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`test_activation.py=8757ca43a349957f6c28479cc139e37c58058fdf13b078925403ca1ac002eb2f`。
- cleanup: test temporary stateは終了時に回収。registry/menu install、実Codex/Claude、対象project変更なし。承認snapshotはclean、元sourceのHEAD/statusは事前状態を保持。
- next: close直後に `bd ready` から次の既存10分製品Issueだけをclaimする。親umbrellaはopen。

### `sm-62a.5.5` 実行記録

- implementation: Explorer個別skill leafのpublic `launch_context_leaf` 実行中に強制中断を注入し、contract、schema、process markerが存在する実際の中断点を作成する。その後、別の新 `ActivationEngine` のpublic `plan` entryから復旧するE2Eへ更新。
- recovery evidence: 復旧後の `interrupted` / `*-not-guaranteed.json` は正確に1件。terminal eventは `interrupted`。二度目のpublic entry後もnegative evidence bytesと件数は不変で、復旧はexactly-once。
- cleanup evidence: contractは監査用に保持。`*-verified.json`、`*-process.json`、`*-schema.json`、`*-output.json`、`*-events.jsonl` はすべて残留なし。
- focused test: `python -m unittest -v tests.test_activation.ActivationEndToEndTest.test_new_public_entry_recovers_interruption_exactly_once` → 1 E2E PASS (1.318s)。
- activation regression: `python -m unittest tests.test_activation` → 36 tests PASS (36.540s)。
- full regression: `python -m unittest discover -s tests` → 68 tests PASS (49.269s)。`git diff --check` PASS。
- evidence SHA-256: `activation.py=07c1ce4056a91d907cb62aeb7f0c7c85ba25b09ac36ac07a99830e806701edae`、`ui.py=1c97eab31033faaceea2abe6c47962c50c1a3dc5fb0defbaf177763ecc8c1b78`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`test_activation.py=7a51ecaad1727dbf29278189f9dfd100543c2713f76949029e4054a8bd5b3711`。
- preservation: registry/menu install、実Codex/Claude、対象project変更、commit/pushなし。承認snapshot `c7747bba...` はclean。元source HEAD/statusは事前状態を保持。
- next: close直後に `bd ready` を実測し、次の既存10分製品Issueだけをclaimする。親umbrellaはopen。

### `sm-62a.5.6` 実行記録

- E2E: Background `%V` の個別 `bounded-answer → Claude` leafを明示選択。未対応runtimeをerror UI 1回・`unsupported_runtime` rejected 1件でfail-closedにし、Codex fallback/executeが0回であることを検証。
- project preservation: `project.txt`、`.agents/existing.txt`、`.claude/existing.txt`、`.skill-magnet-old-state.json` の相対path/bytes snapshotが実行前後で完全一致。Codex/Claude target install directoryも未作成。
- artifact evidence: launch contract、evidence、process markerは未作成。rejected event以外の副作用なし。
- focused test: `python -m unittest -v tests.test_activation.ActivationEndToEndTest.test_individual_claude_leaf_has_one_error_and_zero_project_side_effects` → 1 E2E PASS (0.509s)。
- full regression: `python -m unittest discover -s tests` → 69 tests PASS (47.621s)。`git diff --check` PASS。
- evidence SHA-256: `activation.py=07c1ce4056a91d907cb62aeb7f0c7c85ba25b09ac36ac07a99830e806701edae`、`ui.py=1c97eab31033faaceea2abe6c47962c50c1a3dc5fb0defbaf177763ecc8c1b78`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`test_activation.py=67987aacdfe30a398dd4770ae0c202d19d6bad839837d7e8e01121099eafd66b`。
- cleanup/preservation: temporary project/stateはテスト終了時に回収。registry/menu install、実Codex/Claude、実ユーザーproject変更、commit/pushなし。承認snapshotはclean、元source HEAD/statusは事前状態を保持。
- next: close直後に `bd ready` を実測し、次の既存10分製品Issueだけをclaimする。親umbrellaはopen。

### `sm-62a.5.7.1` / `SM-SK-001` 実行記録

- implementation: success/failure/interrupted terminalごとにcontractの `attempt_id` を保持し、`events/<contract-id>-lifecycle.jsonl` へsanitized terminal eventを原子的に1行記録。terminal event IDをlifecycleとverified/negative evidenceで相互参照する。raw prompt/model outputは永続化しない。
- real Codex: 隔離target `C:\Projects\skill-magnet\.e2e-target\sm-sk-001` から、承認snapshot `c7747bba...` の `codex-auth-boundary-selection` 一件だけを `codex exec --ephemeral --ignore-user-config --ignore-rules --sandbox read-only` へ送達。exit 0、stdout/stderr/UIなし、`verified_applied`。
- IDs: attempt `7a7f0972f6d74b06ac7c845169d95502`、contract `899d38fc86464b71801c8368bb841c95`、terminal event `591ceaf5b94f4099bc616c035dc5d7d9`。
- skill assertion: `result.auth_boundary == non_interactive_process_scoped_api_key` PASS。skill read evidenceとapplication evidenceは `codex-auth-boundary-selection` 一件だけで、他8 skill混入なし。
- evidence hashes: contract `b3722f2546f076b67249302bc8f26e4236bd88e4b2791084ebc68b9765bd13c1`、lifecycle `530afcd55d2bff905d8a53eb108a63bec096631638771935245130ad5ed1a095`、verified evidence `c657f747cd146465d6305d49f9bda36506d76e3cb3f2dae4ea7f2cffee76b1c9`。
- focused lifecycle regression: explicit silent success + interruption exactly-onceの2 tests PASS (2.369s)。full regression `python -m unittest discover -s tests` → 69 tests PASS (47.345s)。`git diff --check` PASS。
- source hashes: `activation.py=9de7f74c0678c2679e6382cda4c1343ce1caaae97eeb328af781ab67ea0f9d25`、`ui.py=1c97eab31033faaceea2abe6c47962c50c1a3dc5fb0defbaf177763ecc8c1b78`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`test_activation.py=0665a0e3ac3228a39781e63ede4ad6cb88e9c9c81f29c32b3598fa6d2f8f7d98`。
- cleanup/preservation: final evidence stateにcontract/lifecycle/verifiedだけを保持し、schema/output/raw events/process markerは0。隔離target fileは0→0。registry/menu install、元source、実ユーザーproject、commit/push変更なし。承認snapshotはclean。
- next: close直後に `bd ready` から次の既存10分個別skill Issueだけをclaimする。

### `sm-62a.5.7.2` / `SM-SK-002` 実行記録

- real Codex: 隔離target `C:\Projects\skill-magnet\.e2e-target\sm-sk-002` から、承認snapshot `c7747bba...` の `codex-bounded-subagents` 一件だけをread-only/ephemeral Codexへ送達。exit 0、stdout/stderr/UIなし、`verified_applied`。
- IDs: attempt `cc062287382d4334bc2295158a77c5ff`、contract `2ea3a004963b4dc0a76f040a2ec906ae`、terminal event `8e476bf24a9f4f40b43aa65a43d31a21`。
- skill assertion: `result.subagent_boundary == parallel_read_heavy_single_writer` PASS。skill read/application evidenceのkeyは `codex-bounded-subagents` 一件だけで、他8 skill混入なし。
- evidence hashes: contract `c434b7b721fa36423ec0d4c4158ad31be4c0bcc80ecb44a3e0a126fa63f2255c`、lifecycle `28a63eb66c1bf9fd2170160aefd726477b37f410556e6d361bd5274b3a54f3e5`、verified evidence `8af42c09bbd37aaa1f847a4e0536e40a2324f9aad0d3ba3243b8c6f04b2f7a29`。
- focused regression: both-root個別contract伝播 + silent verified lifecycleの2 tests PASS (4.266s)。full regression `python -m unittest discover -s tests` → 69 tests PASS (47.099s)。`git diff --check` PASS。
- source hashes: `activation.py=9de7f74c0678c2679e6382cda4c1343ce1caaae97eeb328af781ab67ea0f9d25`、`ui.py=1c97eab31033faaceea2abe6c47962c50c1a3dc5fb0defbaf177763ecc8c1b78`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`test_activation.py=0665a0e3ac3228a39781e63ede4ad6cb88e9c9c81f29c32b3598fa6d2f8f7d98`。
- cleanup/preservation: contract/lifecycle/verifiedだけを保持。schema/output/raw events/process markerは0、隔離target fileは0→0。registry/menu、元source、実ユーザーproject、commit/push変更なし。承認snapshotはclean。
- next: close直後に `bd ready` から次の既存10分個別skill Issueだけをclaimする。

### `sm-62a.5.7.3` / `SM-SK-003` 実行記録

- real Codex: 隔離target `C:\Projects\skill-magnet\.e2e-target\sm-sk-003` から、承認snapshot `c7747bba...` の `codex-ci-patch-handoff` 一件だけをread-only/ephemeral Codexへ送達。exit 0、stdout/stderr/UIなし、`verified_applied`。
- IDs: attempt `0e110cc2d40445ffaa3498e9fe1d89b5`、contract `f076e117d5d4429f9ea4e29a9a101f42`、terminal event `818993d935d142cba4b19590fa201158`。
- skill assertion: `result.ci_handoff == read_only_generation_binary_patch_separate_write_job` PASS。skill read/application evidenceのkeyは `codex-ci-patch-handoff` 一件だけで、他8 skill混入なし。
- evidence hashes: contract `0b13496174e2a1ecf55946728151356d41ba16d2cdcffc8fb9b58f60741bfc94`、lifecycle `466572eda48656221712039fcb373b1660732e2c5d0ffd95c5415207526614ec`、verified evidence `975754a428e15c823e44678a2ad9bf610ac34da734c28855f99ab1229989fc7c`。
- focused regression: both-root個別contract伝播 + silent verified lifecycleの2 tests PASS (4.302s)。full regression `python -m unittest discover -s tests` → 69 tests PASS (47.113s)。`git diff --check` PASS。
- source hashes: `activation.py=9de7f74c0678c2679e6382cda4c1343ce1caaae97eeb328af781ab67ea0f9d25`、`ui.py=1c97eab31033faaceea2abe6c47962c50c1a3dc5fb0defbaf177763ecc8c1b78`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`test_activation.py=0665a0e3ac3228a39781e63ede4ad6cb88e9c9c81f29c32b3598fa6d2f8f7d98`。
- cleanup/preservation: contract/lifecycle/verifiedだけを保持。schema/output/raw events/process markerは0、隔離target fileは0→0。registry/menu、元source、実ユーザーproject、commit/push変更なし。承認snapshotはclean。
- next: close直後に `bd ready` から次の既存10分個別skill Issueだけをclaimする。

### `sm-62a.5.7.4` / `SM-SK-004` 実行記録

- real Codex: 隔離target `C:\Projects\skill-magnet\.e2e-target\sm-sk-004` から、承認snapshot `c7747bba...` の `codex-context-entry-routing` 一件だけをread-only/ephemeral Codexへ送達。exit 0、stdout/stderr/UIなし、`verified_applied`。
- IDs: attempt `bcdf6a24cbcb49759bdf64f7fd6f7694`、contract `7432986735e14632af1f908c5cbf7e6e`、terminal event `e306f6608eb945e9b289e745fb577398`。
- skill assertion: `result.context_entry == minimal_entry_with_task_contract` PASS。skill read/application evidenceのkeyは `codex-context-entry-routing` 一件だけで、他8 skill混入なし。
- evidence hashes: contract `dae37ee31669e53ef7a0932b6e9eb031fa18aafd12c846f2a161fa6d7128c8b5`、lifecycle `cdcccb122f7c9e326d6a7a50964c75e63d90d680783400c35518142daf506747`、verified evidence `8f1da0e5b42677fcdd082fc40680f535f76c6a4d637bf7b1da9c0db927b3ed01`。
- focused regression: both-root個別contract伝播 + silent verified lifecycleの2 tests PASS (4.258s)。full regression `python -m unittest discover -s tests` → 69 tests PASS (47.321s)。`git diff --check` PASS。
- source hashes: `activation.py=9de7f74c0678c2679e6382cda4c1343ce1caaae97eeb328af781ab67ea0f9d25`、`ui.py=1c97eab31033faaceea2abe6c47962c50c1a3dc5fb0defbaf177763ecc8c1b78`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`test_activation.py=0665a0e3ac3228a39781e63ede4ad6cb88e9c9c81f29c32b3598fa6d2f8f7d98`。
- cleanup/preservation: contract/lifecycle/verifiedだけを保持。schema/output/raw events/process markerは0、隔離target fileは0→0。registry/menu、元source、実ユーザーproject、commit/push変更なし。承認snapshotはclean。
- next: close直後に `bd ready` から次の既存10分個別skill Issueだけをclaimする。

### `sm-62a.5.7.5` / `SM-SK-005` 実行記録

- real Codex: 隔離target `C:\Projects\skill-magnet\.e2e-target\sm-sk-005` から、承認snapshot `c7747bba...` の `codex-egress-surface-governance` 一件だけをread-only/ephemeral Codexへ送達。exit 0、stdout/stderr/UIなし、`verified_applied`。
- IDs: attempt `d87ccd14c21c457ea86adea5b1538bda`、contract `fc1a4c99eddb42f0b43bcf224815fa93`、terminal event `74911f44bd694d79b41224e4f27ca120`。
- skill assertion: `result.egress_control == per_surface_controls_no_bypass` PASS。skill read/application evidenceのkeyは `codex-egress-surface-governance` 一件だけで、他8 skill混入なし。
- evidence hashes: contract `8e7f9ae0ccc00896849582bf3f74fd3e53b6b0290fecff9deb72a4c74c0303cb`、lifecycle `f8e2cc28914b0154da1b242470a9d4277392b1f546d039b30667d7ebd2e39bd5`、verified evidence `9cd77e6ea50dcb2413e467642c47d587d2aac8f1bfab1c4ce1afed48ecfc1c98`。
- focused regression: both-root個別contract伝播 + silent verified lifecycleの2 tests PASS (4.004s)。full regression `python -m unittest discover -s tests` → 69 tests PASS (44.605s)。`git diff --check` PASS。
- source hashes: `activation.py=9de7f74c0678c2679e6382cda4c1343ce1caaae97eeb328af781ab67ea0f9d25`、`ui.py=1c97eab31033faaceea2abe6c47962c50c1a3dc5fb0defbaf177763ecc8c1b78`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`test_activation.py=0665a0e3ac3228a39781e63ede4ad6cb88e9c9c81f29c32b3598fa6d2f8f7d98`。
- cleanup/preservation: contract/lifecycle/verifiedだけを保持。schema/output/raw events/process markerは0、隔離target fileは0→0。registry/menu、元source、実ユーザーproject、commit/push変更なし。承認snapshotはclean。
- next: close直後に `bd ready` から次の既存10分個別skill Issueだけをclaimする。

### `sm-62a.5.7.6` / `SM-SK-006` 実行記録

- real Codex: 隔離target `C:\Projects\skill-magnet\.e2e-target\sm-sk-006` から、承認snapshot `c7747bba...` の `codex-exec-io-contract` 一件だけをread-only/ephemeral Codexへ送達。exit 0、stdout/stderr/UIなし、`verified_applied`。
- IDs: attempt `a88c90724f34498abbefb9646653aca4`、contract `cdf0d2fde40a4845b60fdd9d7cb3d271`、terminal event `b043461fb97649d0aae0265118c6550a`。
- skill assertion: `result.exec_io == stdin_task_jsonl_events_schema_final_nonzero_fail` PASS。skill read/application evidenceのkeyは `codex-exec-io-contract` 一件だけで、他8 skill混入なし。
- evidence hashes: contract `23f2013ab88f695a8988c43bb8dd149ad79958b67d5a8c0a716b30475683ee5f`、lifecycle `96080ff381b5dfed5cfcc53d304013e58702eacb1085e9b745df43a51adf0cf2`、verified evidence `f7249a7151dcb0760e839b8ac014bc4413161be5353a550b4689f5d35dcd2b35`。
- focused regression: both-root個別contract伝播 + silent verified lifecycleの2 tests PASS (3.993s)。full regression `python -m unittest discover -s tests` → 69 tests PASS (44.708s)。`git diff --check` PASS。
- source hashes: `activation.py=9de7f74c0678c2679e6382cda4c1343ce1caaae97eeb328af781ab67ea0f9d25`、`ui.py=1c97eab31033faaceea2abe6c47962c50c1a3dc5fb0defbaf177763ecc8c1b78`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`test_activation.py=0665a0e3ac3228a39781e63ede4ad6cb88e9c9c81f29c32b3598fa6d2f8f7d98`。
- cleanup/preservation: contract/lifecycle/verifiedだけを保持。schema/output/raw events/process markerは0、隔離target fileは0→0。registry/menu、元source、実ユーザーproject、commit/push変更なし。承認snapshotはclean。
- next: close直後に `bd ready` から次の既存10分個別skill Issueだけをclaimする。

### `sm-62a.5.7.7` / `SM-SK-007` 実行記録

- real Codex: 隔離target `C:\Projects\skill-magnet\.e2e-target\sm-sk-007` から、承認snapshot `c7747bba...` の `codex-execution-mode-routing` 一件だけをread-only/ephemeral Codexへ送達。exit 0、stdout/stderr/UIなし、`verified_applied`。
- IDs: attempt `d314383b16a14fef88e156ba22b3e145`、contract `47620c23a0df4ff0bf6020f8e845e921`、terminal event `6f9caabba36146cbab46ea76c193eb4e`。
- skill assertion: `result.execution_mode == non_interactive_codex_exec` PASS。skill read/application evidenceのkeyは `codex-execution-mode-routing` 一件だけで、他8 skill混入なし。
- evidence hashes: contract `f606a8b07aa0fcceedf0b02eceb1a360223f41bed9598026e09ffc8faca12f1b`、lifecycle `a432aef92ee19c66362ad94da1541c2cc40a3cbdccaa8dcaaf4ebcddbfe7ca3c`、verified evidence `f14d93b30506ac2f6cb628fdbbfb00cd9a1334ef6f725ac1e937c435d5a58c22`。
- focused regression: both-root個別contract伝播 + silent verified lifecycleの2 tests PASS (3.973s)。full regression `python -m unittest discover -s tests` → 69 tests PASS (44.739s)。`git diff --check` PASS。
- source hashes: `activation.py=9de7f74c0678c2679e6382cda4c1343ce1caaae97eeb328af781ab67ea0f9d25`、`ui.py=1c97eab31033faaceea2abe6c47962c50c1a3dc5fb0defbaf177763ecc8c1b78`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`test_activation.py=0665a0e3ac3228a39781e63ede4ad6cb88e9c9c81f29c32b3598fa6d2f8f7d98`。
- cleanup/preservation: contract/lifecycle/verifiedだけを保持。schema/output/raw events/process markerは0、隔離target fileは0→0。registry/menu、元source、実ユーザーproject、commit/push変更なし。承認snapshotはclean。
- next: close直後に `bd ready` から次の既存10分個別skill Issueだけをclaimする。

### `sm-62a.5.7.8` / `SM-SK-008` 実行記録

- real Codex: 隔離target `C:\Projects\skill-magnet\.e2e-target\sm-sk-008` から、承認snapshot `c7747bba...` の `codex-mcp-control-plane` 一件だけをread-only/ephemeral Codexへ送達。exit 0、stdout/stderr/UIなし、`verified_applied`。
- IDs: attempt `b5bdac5d5c124a7e8eaaff90efd2863a`、contract `6bc34a2262a74070a9604e3eef5eefa2`、terminal event `f943ec397e564fe2b02eb3e9664d039e`。
- skill assertion: `result.mcp_control == required_read_only_allowlist_write_denied` PASS。skill read/application evidenceのkeyは `codex-mcp-control-plane` 一件だけで、他8 skill混入なし。
- evidence hashes: contract `58bc8e07f2c2f92b884bb069f1a842fee8340e79e49019a4ae6984adc10660d6`、lifecycle `d5d4589f34e9670bc8913624215104169dd4edfa0ee82982d578cd398870c8cf`、verified evidence `bafecfcbac1cb00f25744ba3a2676dd9d20b86ab6f526f148f4cd1edcf562d4c`。
- focused regression: both-root個別contract伝播 + silent verified lifecycleの2 tests PASS (3.973s)。full regression `python -m unittest discover -s tests` → 69 tests PASS (44.961s)。`git diff --check` PASS。
- source hashes: `activation.py=9de7f74c0678c2679e6382cda4c1343ce1caaae97eeb328af781ab67ea0f9d25`、`ui.py=1c97eab31033faaceea2abe6c47962c50c1a3dc5fb0defbaf177763ecc8c1b78`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`test_activation.py=0665a0e3ac3228a39781e63ede4ad6cb88e9c9c81f29c32b3598fa6d2f8f7d98`。
- cleanup/preservation: contract/lifecycle/verifiedだけを保持。schema/output/raw events/process markerは0、隔離target fileは0→0。registry/menu、元source、実ユーザーproject、commit/push変更なし。承認snapshotはclean。
- next: close直後に `bd ready` から次の既存10分個別skill Issueだけをclaimする。

### `sm-62a.5.7.9` / `SM-SK-009` 実行記録

- real Codex: 隔離target `C:\Projects\skill-magnet\.e2e-target\sm-sk-009` から、承認snapshot `c7747bba...` の `codex-sandbox-approval-boundary` 一件だけをread-only/ephemeral Codexへ送達。exit 0、stdout/stderr/UIなし、`verified_applied`。
- IDs: attempt `3b0740387388407ebdfceb3672589522`、contract `498e2d8b946141649915c3db5362a7ec`、terminal event `fdd7828dafa3413fb54616b7ea237423`。
- skill assertion: `result.sandbox_boundary == read_only_no_midrun_approval` PASS。skill read/application evidenceのkeyは `codex-sandbox-approval-boundary` 一件だけで、他8 skill混入なし。
- evidence hashes: contract `faaba854d438e7bfdde0cac8d56827958ad1d5cec8dff1e1a34f6b79c9c59fbe`、lifecycle `c67117a324ab6866f10ecbbacb3e595c769633c36f198edaf023a3d6da577d09`、verified evidence `d1c426f3da96bc2718589cb7ec787667d0ae5de17bbb7bcea7fe7b783a11faee`。
- focused regression: both-root個別contract伝播 + silent verified lifecycleの2 tests PASS (4.004s)。full regression `python -m unittest discover -s tests` → 69 tests PASS (44.842s)。`git diff --check` PASS。
- source hashes: `activation.py=9de7f74c0678c2679e6382cda4c1343ce1caaae97eeb328af781ab67ea0f9d25`、`ui.py=1c97eab31033faaceea2abe6c47962c50c1a3dc5fb0defbaf177763ecc8c1b78`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`test_activation.py=0665a0e3ac3228a39781e63ede4ad6cb88e9c9c81f29c32b3598fa6d2f8f7d98`。
- cleanup/preservation: contract/lifecycle/verifiedだけを保持。schema/output/raw events/process markerは0、隔離target fileは0→0。registry/menu、元source、実ユーザーproject、commit/push変更なし。承認snapshotはclean。
- next: close直後に `bd ready` から次の既存10分製品Issueだけをclaimする。親umbrellaは全子/集約ゲートを確認するまでcloseしない。

### `sm-62a.5.8` / 9 skills個別E2E行列の集約検証

- aggregate command: Python read-only inline validatorで `skill-magnet.json` の9 skill順を正本にし、固定commitから `git show <commit>:<skill>/SKILL.md|acceptance.json` のbyte digestを再計算。各 `.e2e-state/sm-sk-00N-evidence` のcontract 1件、sanitized lifecycle 1行、verified evidence 1件と結果MD行を機械照合した。
- aggregate result: `PASS`。skills/verified_applied/unique contracts/unique attempts/unique terminal IDs/unique evidence paths はすべて `9`。`selection_kind=skill`、`selected_skill_id`、単一 `skill_ids`、read evidence、application evidence、acceptance assertionが各skillと一対一。
- negative checks: implicit pack selection `0`、cross-skill contamination `0`、missing/duplicate `0`、schema/output/raw events/process residual `0`、隔離target file `0`。9件すべて固定commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc` とinstruction/acceptance digestが一致。
- artifact hash: contract/lifecycle/verified 27ファイルを相対path順に並べ、各file SHA-256行をUTF-8/LFで連結した集合SHA-256は `03bdf155ce352f66a709c679b104fd0eb02d965c4294063337e264ca7f7f1d3c`。
- full regression: `python -m unittest discover -s tests` → 69 tests PASS (44.531s)。`git diff --check` PASS。
- source hashes: `activation.py=9de7f74c0678c2679e6382cda4c1343ce1caaae97eeb328af781ab67ea0f9d25`、`ui.py=1c97eab31033faaceea2abe6c47962c50c1a3dc5fb0defbaf177763ecc8c1b78`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`test_activation.py=0665a0e3ac3228a39781e63ede4ad6cb88e9c9c81f29c32b3598fa6d2f8f7d98`。
- cleanup/preservation: 9件の監査用contract/lifecycle/verifiedのみ保持。承認snapshotはclean。registry/menu、元source、実ユーザーproject、commit/push変更なし。
- next: close直後に `bd ready` を実測し、次の既存10分製品Issueだけをclaimする。親umbrellaは依存ゲートどおり扱う。

### `sm-62a.5.9` / `SM-INT-008` Explorer menu cancel E2E

- implementation/test contract: `test_explorer_menu_cancel_before_leaf_has_zero_side_effects` を追加。Directory `%1` / Background `%V` の両rootでroot/pack/skill階層を開き、Codex/Claude runtime leafを選ばず閉じる操作を模擬する。
- executable boundary: command keyは各rootの `skill-*\\runtime-*\\command` 4 leafだけに存在し、root/pack/skillの非leaf nodeには実行commandがないことを検証。leaf selection eventを発生させないためrunner/processは0回。
- zero-side-effect evidence: runner `0`、Codex/Claude process `0`、contract `0`、event `0`、evidence `0`、error UI `0`。project、`.agents`、`.claude`、旧stateのrelative path/bytesは実行前後で完全一致し、既定install directoryも未作成。
- focused E2E: `python -m unittest -v tests.test_activation.ActivationEndToEndTest.test_explorer_menu_cancel_before_leaf_has_zero_side_effects` → 1 PASS (0.537s)。
- full regression: `python -m unittest discover -s tests` → 70 tests PASS (45.008s)。`git diff --check` PASS。
- artifact/source hashes: `test_activation.py=0a82882fe83541a88d6feb7c4d9bbb7bfd504646d3f3696d8836baecf8423a66`、`platforms.py=6056a3bf7ba54acef50d65014fc009730e8179123b397ae326955e5c8be7335d`、`activation.py=9de7f74c0678c2679e6382cda4c1343ce1caaae97eeb328af781ab67ea0f9d25`、`ui.py=1c97eab31033faaceea2abe6c47962c50c1a3dc5fb0defbaf177763ecc8c1b78`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`。
- cleanup/preservation: unittest temporary directoryは終了時に回収。承認snapshotはclean。registry/menu install、実ユーザーproject、commit/push変更なし。
- next: close直後に `bd ready` を実測し、次の既存10分製品Issueだけをclaimする。親umbrella/監査は先行しない。

### `sm-62a.6.1` / Explorer試験registry snapshot・復元

- scope: 隔離stateと空のsafe targetを使い、同一PowerShell cycle内で既存登録をexport、製品CLIでtemporary install、temporary export、製品CLIでuninstall、元exportをimport、restore exportの順に実行した。実ユーザーproject、skill配置、source checkoutは変更していない。
- absolute keys: `HKCU\Software\Classes\Directory\shell\SkillMagnet`、`HKCU\Software\Classes\Directory\Background\shell\SkillMagnet`。
- product commands: `python -m skill_magnet --config skill-magnet.json --state-dir <isolated> install-context-menu --platform windows --confirm` と対応する `uninstall-context-menu --platform windows --confirm` はともにexit 0。`--confirm`なしの事前試行は安全拒否され、registry変更前にexitした。
- snapshot/temporary diff: Directory before `e85e28f67cdf0e54e85032712a5af4a3c487439a5726a607752e2e18a22ad657`、temporary `a6b40d71bbfc562b40d1a1c48d8b2a46dee60bcd3f08fa793cf07c27003c4447`、diff 164 lines。Background before `e2ae9353434de03c1246ccba29510ac7e2025a5288d4c57c03b6ee71b507a0be`、temporary `dc6d35ab484e73e66a504dce48fbdb653466816ef46197f2ef1c97e8c4d11084`、diff 164 lines。descendant countは両rootとも `7 -> 57`。
- restore: Directory restore SHA-256 `e85e28f67cdf0e54e85032712a5af4a3c487439a5726a607752e2e18a22ad657`、Background restore SHA-256 `e2ae9353434de03c1246ccba29510ac7e2025a5288d4c57c03b6ee71b507a0be`。両方ともbefore exportとbyte-equivalentで、descendant countは `7/7` へ復元。
- residual/cleanup: cycle終了時の追加process ID `0`、隔離state artifact `0`、safe target file `0`。検証後に2つの試験temporary directoryを絶対path・temp-base・issue固有leaf名で検証して削除し、存在 `false/false`、`reg` process `0` を再確認。両registry keyは存在し、descendant count `7/7` を維持。
- regression: `python -m unittest discover -s tests` -> 70 tests PASS (49.955s)。`git diff --check` PASS（既存LF/CRLF warningのみ、errorなし）。
- source hashes: `activation.py=9de7f74c0678c2679e6382cda4c1343ce1caaae97eeb328af781ab67ea0f9d25`、`ui.py=1c97eab31033faaceea2abe6c47962c50c1a3dc5fb0defbaf177763ecc8c1b78`、`cli.py=ec4e724aade536841afa8485e2a20b78a4d0b36503f8e37bb78fb807468b119b`、`platforms.py=6056a3bf7ba54acef50d65014fc009730e8179123b397ae326955e5c8be7335d`、`test_activation.py=0a82882fe83541a88d6feb7c4d9bbb7bfd504646d3f3696d8836baecf8423a66`。
- preservation/next: 既存dirty差分、承認snapshot、9-skill evidenceを保持し、commit/pushなし。close後に `bd ready` を実測し、次の既存10分製品Issueだけをclaimする。

### `sm-62a.6.2` / `SM-INT-002` Directory特殊path実Codex

- defect/fix: 初回のdefault `codex.cmd`実行は、runtime wrapperを `cmd /s /c <single command string>` へ再包装したため、特殊projectの空白で `unexpected argument '002'` となり `output_failed`。`activation.py` のwrapper境界を `cmd /d /c <wrapper> <argv...>` のargv配列へ修正した。同一prompt/schemaの生CLI診断は修正形でexit 0となり、Windows `.cmd` wrapperが特殊projectを単一`--cd` argvとして受ける回帰testを追加した。失敗contract/lifecycle/negative evidenceはartifact policyどおり保持し、`verified_applied`へ書き換えていない。
- real leaf: temporary individual-skill registryのDirectory `%1` leafから、`C:\Projects\skill-magnet\.e2e-target\SM INT 002 日本語 & ( ) ' ! ^ # %` をshellなしのWindows CreateProcessへ一度送達。registry commandを`windows_leaf_command_argv`の期待vectorと照合した `argv_contract_exact=true`。config、project、pack、skill、runtime、commit、instruction/acceptance digestはすべて独立argvで一致した。
- selection/provenance: `codex-pmo-skills` / `codex-auth-boundary-selection` / `codex`、fixed commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc`、instruction `90c8229b22400e8e08f31cd4b7d808fafe808bd35fa1d72235f40da7db1f072e`、acceptance `a993d08c8dfd9e4739bd9e6150772e2c45a67c40778d0051a35ceecdad27d92d`。
- verified evidence: attempt `c40a80cb71164784986f45674b9ce67d`、contract `c8b97752a0ad49b2ab64cfc70e97b52c`、terminal event `adc46de020464884a97fd5ad0eb3b208`、status `verified_applied`、`result.auth_boundary=non_interactive_process_scoped_api_key`。leaf exit `0`、stdout `0`、stderr `0`で成功UI/terminal表示なし。
- evidence paths/hashes: contract `.e2e-state/sm-int-002-final/launch-contracts/c8b97752a0ad49b2ab64cfc70e97b52c.json` = `b9e811bc59e9acd9b75caab1c56db1948acbbd2aeabca59b13c8998b924a0d93`、lifecycle `.e2e-state/sm-int-002-final/events/c8b97752a0ad49b2ab64cfc70e97b52c-lifecycle.jsonl` = `67ce0a34e60458716d29a4f7bd17589184812904000d7863f79a196c1cb4b70c`、verified `.e2e-state/sm-int-002-final/evidence/c8b97752a0ad49b2ab64cfc70e97b52c-verified.json` = `ef66d8dc7f096992bebdee0dc8e1dee5bbdc5c26e4244616d7e8a07cd8a53bdf`。
- registry cycle: absolute keysは `HKCU\Software\Classes\Directory\shell\SkillMagnet` と `HKCU\Software\Classes\Directory\Background\shell\SkillMagnet`。before SHA-256は Directory `e85e28f67cdf0e54e85032712a5af4a3c487439a5726a607752e2e18a22ad657` / Background `e2ae9353434de03c1246ccba29510ac7e2025a5288d4c57c03b6ee71b507a0be`、temporaryは `e3391f68274ed1fcaf15fc57cc932de88e9b4fa3c6f0643f56f379eb07651a5b` / `cbdea01652b616fec2a13dcaa0887687b4c8fa50d8d80167e19e6c24fc94dd93`、restoreはbeforeとbyte-equivalent。descendant `7/7 -> 57/57 -> 7/7`、install/uninstall exit `0/0`。
- cleanup/preservation: safe targetは開始前空、実行後file `0`、終了時directory削除。temporary configと3 cycleのsnapshot directoryを削除し、issue固有temp residual `0`。final stateはcontract/lifecycle/verifiedの3監査artifactだけで、schema/output/raw events/process marker `0`。実行cycleの追加Codex process `0`。既存registry、実ユーザーproject、承認snapshot、既存dirty差分を保持し、commit/pushなし。
- tests: focused `test_windows_cmd_runtime_preserves_special_project_as_one_argv` + `test_both_registry_roots_preserve_special_absolute_paths_as_single_argv` -> 2 PASS (3.581s)。full `python -m unittest discover -s tests` -> 71 PASS (62.099s)。`git diff --check` PASS（既存LF/CRLF warningのみ）。source SHA-256: `activation.py=9e7162af39c221757e2bca4c85a24749809c4390da71718303a97a250f06eefb`、`test_activation.py=93db82d2a84a77d4f7bc4f91cb0f61a46d1114583158b7fb4c7528189cc1040c`。
- next: close直後に `bd ready` を実測し、次の既存10分製品Issueだけをclaimする。

### `sm-62a.6.3` / `SM-INT-003` Background特殊path実Codex

- real field path: Background `%V` leafへ `C:\Projects\skill-magnet\.e2e-target\SM INT 003 背景 & ( ) ' ! ^ # %` をshellなしCreateProcessで送達し、`windows_leaf_command_argv`との完全一致 `argv_exact=true`。`codex-pmo-skills` / `codex-auth-boundary-selection` / Codex / fixed `c7747bba0bc391316aa558b3b4e8dd412045d2dc`。
- verified: attempt `e295b93018224f16bcd7bf7b31159616`、contract `de47cd747f8a4f17aa25af40549913df`、terminal `7c63f336caf942e4b69f2acbd8253a92`、`verified_applied`、`result.auth_boundary=non_interactive_process_scoped_api_key`。leaf exit 0、stdout/stderr/UI 0。
- evidence: contract `f2d31968cb1763b370ac921af18e0477a71aba1c489b506ba4ae74fb120a8fcd`、lifecycle `eca3ba38a38fee0cb059c27ea9565eb06b84d6a8ce5952ab0a69eeecb614984d`、verified `31a636263b0701a06465a7eedf1fe56a6f2591ecffa557b57deab9570cb6c79e`（各 `.e2e-state/sm-int-003-final` 配下）。
- registry/cleanup: Directory/Background before `e85e28f6...` / `e2ae9353...`、temporary `da5ca517...` / `6f430ec1...`、restoreはbeforeとbyte-equivalent、descendant `7/7 -> 57/57 -> 7/7`、install/uninstall 0/0。safe target file 0、追加process 0、schema/output/raw/process marker 0。target/config/snapshot temp削除、実ユーザーproject/既存登録/dirty差分保持、commit/pushなし。
- tests: focused 2 PASS (2.434s)、full 71 PASS (51.622s)、`git diff --check` PASS。nextはclose後の既存ready製品Issue。

### `sm-62a.6.4` / `SM-INT-004` Explorer固定版不一致error UI

- injection/field leaf: 承認済み固定commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc` のclean cloneへ空commitを作り、source HEADを `f13bee5c819d47e79e5ae6286cc77efbc99309de` にだけ進めた隔離source/configを使用。Explorer Directory leafから `codex-pmo-skills` / `codex-auth-boundary-selection` / Codexを選択した。
- final field attempt: `eedcf40be80144b9b6a9512d512f8f5d`。Codex AI process/contract/evidence/verified_appliedはすべて `0`、terminal rejected eventは `.e2e-state/sm-int-004-final/events/eedcf40be80144b9b6a9512d512f8f5d-rejected.json` の一件で、`status=rejected`、`reason=preflight_validation_failed`、SHA-256 `5be3bfda164a4a50e8dfd4ebbfb95a6322045787ebd680ea1b6245eb24ec51d9`。
- actual UI: Explorer実機上で Skill Magnet error dialogは一回だけ。本文は expected `c7747bba...` / got `f13bee5c...` を表示し、「新source commitをreview/approve → config expected commitとskill digests更新 → Explorer menu reinstall → selected leaf一致確認 → clean source HEADから再試行」の安全更新ゲート案内を一度だけ表示した。成功UIと背後console/terminalは `0`。Windows登録commandは `pythonw.exe` を使用するよう最小修正した。
- diagnostic attempts: field setup中の先行3回は、非interactive起動中断、誤remote URL、更新案内重複/console発見の診断であり、各rejected eventを監査用に保持した。最終attemptのみをacceptance判定対象とし、全4 rejected eventを書換えず保持。過去3回のerror UI用Python leafがdialog終了後も残留したため、command line/PIDを照合した試験processだけを停止してtarget handleを解放した。Codex AIは一度も起動していない。
- registry restore: absolute rootsは `HKCU\Software\Classes\Directory\shell\SkillMagnet` と `HKCU\Software\Classes\Directory\Background\shell\SkillMagnet`。Directory before/after `e85e28f67cdf0e54e85032712a5af4a3c487439a5726a607752e2e18a22ad657`、Background before/after `e2ae9353434de03c1246ccba29510ac7e2025a5288d4c57c03b6ee71b507a0be` でbyte-equivalent。temporary subtreeをuninstall後に開始前snapshotをimportし、両rootを同一cycleで復元した。
- cleanup/preservation: 隔離source/config/empty target/registry snapshot temp/product tempは削除済み、issue固有process残留 `0`、target project file `0`。rejected event 4件だけを保持。実ユーザーproject、承認snapshot、既存dirty差分を保持し、commit/pushなし。
- tests: focused 3 PASS (1.781s)、full `python -m unittest discover -s tests` 72 PASS (54.100s)、`git diff --check` PASS（既存LF/CRLF warningのみ）。source SHA-256: `activation.py=9e7162af39c221757e2bca4c85a24749809c4390da71718303a97a250f06eefb`、`ui.py=287564c8f1829e5a3cb4145133439fdfd9d03197e61b4667fa28a72a30e0d685`、`cli.py=44895e0cb98f451aad11cee388c4f25574cce2b7549379698786f52f62080584`、`platforms.py=8491c9a7857c84624ee42adef88861eed608bb0ee59a486ef39b0feb191ecc73`、`test_activation.py=ae986587d84a881118ecefff39b94754eb35360e2d90c8473e450b2b83af0c78`。
- next: acceptanceを満たした場合のみcloseし、直後に `bd ready` から次の既存10分製品Issueだけをclaimする。

### `sm-62a.6.5` / `SM-INT-005` Explorer強制中断・public recovery

- actual Explorer: 隔離target `C:\Projects\skill-magnet\.e2e-target\SM INT 005 interrupt` のBackgroundを右クリックし、`Show more options -> Skill Magnet -> Pack: codex-pmo-skills -> Skill: codex-auth-boundary-selection -> Codex` を実選択した。temporary registryは両HKCU rootへ同一cycleだけ登録した。
- forced interruption: process marker出現後に試験runner `pythonw.exe` PID `13844` とその子 `cmd.exe` PID `21180` だけをcommand line/PID照合して強制停止した。attempt `d7518c3cc4b048aa9e6ba5b5e28e46ad`、contract `d188370d527447c08843aa68ba82e07a`、fixed commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc`、prompt hash `ba0f152885e5726b68172ab903a1a6344b2f39ce359552cb1047b89c58455928`、schema hash `2c585387897d67b08dc1454759b62d630815a9ddf0fccc3d58c3d3767f06f825`。
- public recovery: 新しい `ActivationEngine` のpublic `plan(...)` entryを2回呼び、初回だけ中断回収、2回目はidempotentでartifact不変を確認。terminal event `63c0aa1d37669aefc3a2050632dbeba6` は `status=interrupted` / `terminal=true` の1行だけ、negative evidenceも `reason=interrupted` の1件だけ。診断printが戻り値に存在しない `status` / `expected_commit` keyを参照して各1回KeyErrorとなったが、どちらもpublic recovery実行後で、保持artifactのhash/count不変を再照合した。
- retained evidence: contract `.e2e-state/sm-int-005-final/launch-contracts/d188370d527447c08843aa68ba82e07a.json` = `2d8dbe2726907a3b8f17ac3380203cf7e28b6e5def798ab77c9c2164fb7125df`、lifecycle `.e2e-state/sm-int-005-final/events/d188370d527447c08843aa68ba82e07a-lifecycle.jsonl` = `f53ae66f9592f28809b25060edc01c76387d7ddba2f18f954e43c1c8cb0a3f69`、negative `.e2e-state/sm-int-005-final/evidence/d188370d527447c08843aa68ba82e07a-not-guaranteed.json` = `81eff949a3bb9b118458adec3ede1c6d6c2c38644e22b9246f3426a4266ced73`。
- negative/cleanup assertions: contract `1`、lifecycle `1`、negative `1`、verified `0`、schema/output/raw events/process marker `0`。隔離runner/Codex process `0`、target file `0`。Explorer windowを閉じ、temporary config/target/registry snapshotを削除した。
- registry restore: Directory before/after `e85e28f67cdf0e54e85032712a5af4a3c487439a5726a607752e2e18a22ad657`、Background before/after `e2ae9353434de03c1246ccba29510ac7e2025a5288d4c57c03b6ee71b507a0be` でbyte-equivalent。temporary menuをuninstall後に開始前snapshotをimportし、同一cycleで両rootを復元した。
- tests: focused `test_new_public_entry_recovers_interruption_exactly_once` -> 1 PASS (1.422s)。full `python -m unittest discover -s tests` -> 72 PASS (56.697s)。`git diff --check` PASS（既存LF/CRLF warningのみ）。source SHA-256: `activation.py=9e7162af39c221757e2bca4c85a24749809c4390da71718303a97a250f06eefb`、`ui.py=287564c8f1829e5a3cb4145133439fdfd9d03197e61b4667fa28a72a30e0d685`、`cli.py=44895e0cb98f451aad11cee388c4f25574cce2b7549379698786f52f62080584`、`platforms.py=8491c9a7857c84624ee42adef88861eed608bb0ee59a486ef39b0feb191ecc73`、`test_activation.py=ae986587d84a881118ecefff39b94754eb35360e2d90c8473e450b2b83af0c78`。
- preservation/next: 監査用contract/lifecycle/negativeだけを保持。実ユーザーproject、既存registry、承認snapshot、既存dirty差分を保持し、commit/pushなし。close後は `bd ready` の次の既存10分製品Issueだけをclaimする。

### `sm-62a.6.6` / `SM-INT-006` Explorer Claude一回error・最終復元

- actual Explorer: 隔離target `C:\Projects\skill-magnet\.e2e-target\SM INT 006 Claude 日本語 & ( )` のBackgroundから、`Show more options -> Skill Magnet -> Pack: codex-pmo-skills -> Skill: codex-auth-boundary-selection -> Claude` を実選択した。UI画面で対象project、pack、skill、Claude leafを一意に確認した。
- failure UI/evidence: `Claude has no verified runtime adapter; launch blocked` のSkill Magnet error dialogを一回だけ表示し、OKで閉じた。attempt `3c19bc060ab14fd68944b5172a048799` の terminal rejected event一件だけを保持し、`status=rejected`、`reason=unsupported_runtime`、`runtime=claude`、SHA-256 `d721614d6a63cb6fe135ea7a5b0941cc6c62f2ef4eb771c7e720adda8b710749`。
- fail-closed assertions: Codex fallback/Claude AI process `0`、launch contract `0`、evidence `0`、schema/output/raw events/process marker `0`、target file `0`。error dialog終了後に残った試験leaf `pythonw.exe` PID `13284` はcommand lineに隔離config/project/runtime=claudeを照合して停止し、最終issue固有process `0`。
- registry/cleanup: temporary individual-skill menuをuninstallし、開始前snapshotを同一cycleでimport。Directory before/after `e85e28f67cdf0e54e85032712a5af4a3c487439a5726a607752e2e18a22ad657`、Background before/after `e2ae9353434de03c1246ccba29510ac7e2025a5288d4c57c03b6ee71b507a0be` でbyte-equivalent。Explorer windowを閉じ、temporary config/target/registry snapshotを削除した。rejected event一件のみ監査用に保持。
- tests: focused `test_individual_claude_leaf_has_one_error_and_zero_project_side_effects` -> 1 PASS (0.617s)。full `python -m unittest discover -s tests` -> 72 PASS (54.451s)。`git diff --check` PASS（既存LF/CRLF warningのみ）。source SHA-256: `activation.py=9e7162af39c221757e2bca4c85a24749809c4390da71718303a97a250f06eefb`、`ui.py=287564c8f1829e5a3cb4145133439fdfd9d03197e61b4667fa28a72a30e0d685`、`cli.py=44895e0cb98f451aad11cee388c4f25574cce2b7549379698786f52f62080584`、`platforms.py=8491c9a7857c84624ee42adef88861eed608bb0ee59a486ef39b0feb191ecc73`、`test_activation.py=ae986587d84a881118ecefff39b94754eb35360e2d90c8473e450b2b83af0c78`。
- preservation/next: 元registry、実ユーザーproject、承認snapshot、既存dirty差分を保持し、commit/pushなし。close後は `bd ready` を実測し、未完の既存製品成果物がなければ独立監査を外部blockerとして停止する。

### `sm-62a.6.6` 独立監査FAIL差戻し修正

- process fix: Windows failure UIをTk rootからnative `MessageBoxW`へ変更し、Explorer `pythonw.exe` contextはconsoleへ書かずreturn、windowless module entrypointは`main()`後に明示終了する。通常の`python.exe` CLIは従来どおり`SystemExit`を使う。
- actual Explorer Claude rerun: Background leafから `codex-auth-boundary-selection -> Claude` を一回選択。modalを対象windowとして閉じた直後、該当config/project/runtimeの`pythonw.exe=0`（手動killなし）。attempt `d3c52a9dc8a44b11b40613f1222711c8`、`unsupported_runtime` rejected 1件、contract/evidence/temp/Codex fallback/project file 0。
- actual Explorer drift rerun: 隔離source HEAD `f8228ddf697bc683f6d869cddcaf4384a1b7cca0`、expected `c7747bba...` でCodex leafを一回選択。安全な更新手順を含むerror UI 1回を閉じた直後、該当`pythonw.exe=0`（手動killなし）。attempt `9450451905af4273a6e6e1729d683698`、`preflight_validation_failed` rejected 1件、AI/contract/evidence/temp/project file 0。
- target cleanup: 監査指摘の `.e2e-target/sm-sk-001`〜`009` は全件空であることを確認後、issue固有絶対pathを検証して削除。最終residual directory `0`。Claude/drift再試験target、config、diagnostic state、drift source、registry snapshotも削除し、最終監査用rejected event 2件だけ保持。
- registry restore: Directory `e85e28f67cdf0e54e85032712a5af4a3c487439a5726a607752e2e18a22ad657`、Background `e2ae9353434de03c1246ccba29510ac7e2025a5288d4c57c03b6ee71b507a0be` で開始前/復元後byte-equivalent。
- aggregate correction: 文頭の状態、テスト件数、統合行列、未実行範囲、fixed-commit gateを後段証拠へ一致させた。全回帰 `python -m unittest discover -s tests` -> 74 PASS (55.079s)。focused 3 PASS。`git diff --check` PASS後に再監査へ返す。

### `sm-62a.6.6` mandatory recurrence-prevention hardening

- real child boundary: `test_pythonw_entrypoint_hard_exits_after_failure_ui_returns` は実 `pythonw.exe` と製品の `skill_magnet.__main__` bootstrapを子processとして起動し、Claudeのnoninteractive `unsupported_runtime` 境界を通した。PID所有のnative `Skill Magnet` dialogを自動で `WM_CLOSE` した後、timeout内にexit 2で自然終了し、cleanup用terminate fallbackは未使用。rejected 1、contract/evidence/process marker 0、project変更0、完全一致command-line残留0。
- common E2E teardown: `E2ECycleTeardown` が各隔離targetの削除、test-owned PID tree停止、残留assertを一括所有し、assert前の手動`project.rmdir()`を廃止した。`test_four_terminal_paths_reject_live_real_process_residuals` はsuccess / rejected / failure / interruptionごとに実Windows childを生存注入し、実`pythonw.exe`・実`cmd.exe`・test-owned `codex.exe` imageの異なるargv/PID/descendant identityを収集する。各経路でcleanup前guardが必ずFAILし、共通teardown後はtarget/process残留0。別のcmd→codex descendant testもroot PIDだけから異なるchild argvを検出する。
- consistency gate: `integration/explorer_results_gate.py` はmachine ledger、本文test数、13件の詳細 `SM-INT`、canonical `bd --readonly` status/metadataに加え、`.6.7` certificate/classic key/ContextMenu/rollback/Appx/targetの実OS readbackを照合する。detail、OS residual、blocker metadataと古いcanonical/target残留文言を個別に改変したnegative testsはすべてFAIL。
- verification: focused A/B/C 9 tests PASS (43.337s)。full `py -3.12 -m unittest discover -s tests -v` は87 tests PASS (121.293s)。`git diff --check` PASS（改行warningのみ）。
- cleanup/readback: `.6.6` の全隔離test targetは自動cleanup済みでsystem temp配下の `.e2e-target` 子directory 0、hardening markerをexact argvに持つ `pythonw/codex/cmd` process 0。旧Modern rollback targetとtransaction residualは後続の同一transaction復元で0へ戻り、現在値は文頭のstrict readbackを正本とする。
- evidence hashes: `tests/test_activation.py=a5037134e0ff0ea90a043feaadea88e7bc98ba35f67a4d309505488e3532cbc9`、`tests/e2e_guard.py=5a2fab17adef33d9b60595929c01b851963228569f1e488ee2ac1800565affb1`、`tests/test_e2e_guard.py=1ef61f5140490d5314eeb14148120790ffcc3b9aed163edd919fa5da9824616e`、`tests/test_results_gate.py=d0dcce7229cdb5b7ee6325ad2eb4e5dd9516bed73683594292a48760b611ce58`、`integration/explorer_results_gate.py=d24ccbf711b34ba0a0379784cbe37f179d41c4d68bcf2962d9693c9081697cf7`。
- independent re-audit: PASS。監査者がfocused negative、87-test full regression、live blocker gate、strict residual readbackを再現し、Beads `sm-62a.6.6` を2026-08-23T06:04:18Zにcloseした。実装担当は再open/再closeしていない。
- reciprocal reference: 本sectionとBeadsの独立再監査commentを相互参照する。`.6.7` rollback/UAC/registry/certificate/artifactは未操作。

## 今後のclose記録ゲート

### `sm-62a.6.7` 同一rollback transaction最終復元

- resume: 既存`ContextMenu.rollback`とthumbprint `B23FAC247FC7E3553F76EE4B91CCB3928A832623`だけを使用して`rollback-context-menu --confirm`を再開。新規install、別rollback、二重package登録なし。既存transactionは追加UACなしで`rolled_back=true`、rollback point削除まで完走した。
- baseline equivalence: Directory export SHA-256 `e85e28f67cdf0e54e85032712a5af4a3c487439a5726a607752e2e18a22ad657`、Background `e2ae9353434de03c1246ccba29510ac7e2025a5288d4c57c03b6ee71b507a0be`。両方とも開始前baselineとbyte-equivalent。classic subtreeは各8 keysへ復元。
- strict residual: CurrentUser My=0、CurrentUser TrustedPeople=0、LocalMachine TrustedPeople=0、Appx=0、`ContextMenu`=0、`ContextMenu.rollback`=0、transaction一致process=0。空だったissue所有`Modern 空白 & 日本語 (test)` targetを絶対path・owned root・内容0確認後に回収しtarget=0。hash用一時exportも削除済み。
- preservation: 製品コード、実ユーザーproject、他registry subtree、承認snapshotは変更なし。`.6.7` acceptanceの実Explorer folder/background表示・代表leaf成功は既存commentのshadow-observed証拠を保持し、今回の作業はその後の復元だけを完了した。
- isolated revalidation transaction: baseline 0/一致確認後に一つだけ新規transactionを作成（backup SHA-256 `815d16514cca5a0bf6caf23f76915daf3650449160b659ad4df3ef315dbd0a64`、thumbprint `3B35F712F451C24C7EFDDD4273C9317F6303D183`）。実ExplorerのBackgroundとfolder body双方で通常右クリック直下の`Package: PMO`をshadow観測し、package内9 skills×Codex/Claude=18 leafを直接列挙した。
- representative leaf: folder bodyの`codex-auth-boundary-selection | Codex`を実選択。attempt `5f95a4ed5901497a9d8c28251319ff66`、contract `e68aa15bf30241b29287c71ab6339225`、terminal `c6520b74bb4b464c9a866db0b4ee6d17`、`verified_applied`、固定commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc`、`result.auth_boundary=non_interactive_process_scoped_api_key`。process markerは自然回収済み。
- revalidation rollback: 同じtransactionだけをrollback。最終readbackは新thumbprintの全owned store=0、Appx=0、`ContextMenu`/`.rollback`=0、transaction PID=0。唯一のtarget childは`Modern 再検証 & 日本語 (test)`、direct child=true、内容0、transaction対象名一致を最初に証明してから限定削除し、target child=0。classic各8 keys、Directory/Background hashは上記baselineと完全一致。hash proof filesも削除済み。

### `sm-62a.6.7` Codex / Claude 実対話アプリ到達差戻し

- root cause: 旧public pathはCodexを無表示の`codex exec --ephemeral`で終了し、配置・検証成功を利用可能な対話アプリ到達と誤認していた。Claudeは`SUPPORTED_RUNTIMES`に含まれずleaf選択後も起動前に拒否していた。両runtimeに共通する「検証済みsessionを実対話アプリへ引き継ぐ」dispatchが欠落していた。
- fix: Codexは固定skill/schema/acceptanceを実`codex exec`で検証してpersisted thread IDを取得し、実native `codex.exe ... resume <thread-id>`をWindows Terminalへ渡す。Claudeは実`claude.exe --print --output-format json --json-schema ...`の`structured_output`を同じacceptanceで検証し、返されたsession IDを実`claude.exe --resume <session-id>`へ渡す。Windows Terminal起動後はCIMでruntime名+session IDが一致する実PID/ExecutablePath/CommandLineを取得できた場合だけterminal `verified_applied`を確定する。対話handoff失敗は`launch_failed`でfail-closed。
- direct real-runtime proof: Codex PID `37452`、argv `codex.exe --cd C:\Projects\skill-magnet --no-alt-screen resume 01a02d8e-33c9-7012-8c62-4e5738721cc7`。Claude PID `21984`、argv `claude.exe --resume 0b7fca24-e874-4d1c-9e2d-e02b85a13bb3`。双方とも実API/schema/acceptanceを通過した同じsessionで`interactive_ready`。
- public `%1` folder proof: 生成済み`pythonw.exe ... skill_magnet context` entrypointを特殊path `C:\Projects\skill-magnet\.e2e-target\folder 日本語 & (Codex)`へ実行。exit `0`、stdout/stderr `0`、fixed commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc`、`codex-auth-boundary-selection`だけが`verified_applied`。実Codex PID `33552`、session `01a02d92-b260-7891-bcde-7b2eabf1a583`、`result.auth_boundary=non_interactive_process_scoped_api_key`。evidence SHA-256 `90bba72b90503c70185d0d9adab95e80cee2d505c10b6b4214fa9a9aa03d1544`。
- public `%V` background proof: 生成済み同entrypointを `C:\Projects\skill-magnet\.e2e-target\background Japanese & (Claude)`へ実行。exit `0`、stdout/stderr `0`、同じfixed commit/個別skillが`verified_applied`。実Claude PID `39396`、session `eac28756-6e32-43bf-a67a-be8ed1d54571`、同じskill固有assertion。evidence SHA-256 `48d3d0ba2bce760e5949b7f687ef86da7abb92c73b9a70928b457e91f80012e6`。
- user-visible outcome: `WindowsTerminal.exe`は`MainWindowHandle=3149716`、`Responding=True`、実タイトル`✳ Codex auth boundary selection`。Codex/Claudeの各PIDは同Terminal processのchildとして実在し、fixture/fake executableではない。既存Explorerで実証済みのfolder/background→public leaf argv境界は変更せず、そのleaf commandを両context値で実行した。
- tests: focused runtime adapter / 双方のvisible dispatch / stale-ledger negative gate `3/3 PASS`。full `py -3.12 -m unittest discover -s tests` は `90/90 PASS`。live results gate PASS、`git diff --check` PASS（改行warningのみ）。
- source hashes: `activation.py=f11c17a9ef412ada84e3cb35681c0c8e6fbbb25f625703a3dd269702a2592cc4`、`test_activation.py=5dd322c4dcd7fec3a629c7b1323b377ff19f439a8583b7b691a2c0b3f30ea09a`、`explorer_results_gate.py=8b01ae9df28311db6562ce599a6d112adc3c2679044da0f694bc08c806f7a49b`、`test_results_gate.py=9a7a0633c63e1211481c7662af2c4822976e41f281f12cbc9743af10cf8fe552`。
- cleanup/preservation: 証票取得後、試験所有のCodex/Claude PID `37452,21984,33552,39396`だけを終了。session一致runtime残留 `0`、`.e2e-target` child `0`、試験state `0`。owned certificate全store `0`、SkillMagnet Appx `0`、`ContextMenu`/`.rollback`両dirなし、classic Directory/Backgroundは開始前各8 keysを維持。対象project内容、`.agents`、`.claude`、承認snapshot、他registryは変更なし。
- ledger consistency: canonical `.6.7` status/metadata、`.15=open`、test数、OS residualにmachine ledgerを整合。現状態がtarget `0`なのにtarget保持を記す文言、closed監査を未実行と記す文言、canonical `.6.7` statusと食い違う文言をnegative testでFAILさせる。

### `sm-62a.7` 最終独立実装再監査

- result: PASS。strict-readonly Beads readback後、既存 `.7` だけを再開して検証した。
- tests/evidence: `py -3.12 -m unittest discover -s tests` は独立再実行で74/74 PASS、`git diff --check` PASS。Claude/drift再試験のrejected event各1件を実読し、AI、contract、evidence、一時物、project変更がないことを確認した。
- cleanup: error UI終了後の対象`pythonw`/Codex/`cmd` process 0、`.e2e-target`の子directory 0。試験config、target、drift source、registry snapshotは不在。両HKCU rootは元の7 descendant baseline、承認snapshotはcleanな`c7747bba...`を維持した。
- next-state: `.7` をこの記録への相互参照comment付きでcloseする。親epic/umbrellaの扱いは既存Beads依存に従う。

### `sm-62a.12` / Windows 11 Modern package表示の是正

- root cause/fix: sparse packageの `windows.fileExplorerContextMenus` が登録した `IExplorerCommand` は、v1 manifestの各skill/runtimeをroot直下へ列挙していたため、PMO packageの内包skillが通常右クリックの個別項目としてflattenされていた。v2 manifestへ `menu_label` と `selection_kind` を固定し、`selection_kind=package` のrootを `Package: PMO` 一項目、rootのimmediate flyoutを9 skill×Codex/Claude=18 leafへ変更した。classic HKCU fallbackと固定commit/digest/acceptance argvは変更していない。
- actual Explorer Background: `C:\Projects\skill-magnet\.e2e-target` の背景通常右クリックで `Package: PMO` が一項目だけ表示され、トップレベルの個別skillは0。PMOを開くと9 skill×2 runtimeの18 leafを実画面/UI Automationで列挙した。
- actual Explorer Directory: 特殊path `C:\Projects\skill-magnet\.e2e-target\Modern 空白 & 日本語 (test)` のフォルダ本体通常右クリックでも `Package: PMO` 一項目と同じ18 leafを実画面/UI Automationで列挙した。空白、日本語、`&`、括弧を含む選択pathは一つのproject argvとしてleafへ渡る既存契約を維持した。
- representative leaf: Backgroundの `Skill: codex-auth-boundary-selection | Codex` を一回実選択。attempt `f92f489f745c4892bc69322650138623`、contract `3bcff091c14b46aa84e1df9d2eab6062`、terminal event `e0ebec9b72f5404d8e524b36462ecad5`、status `verified_applied`、fixed commit `c7747bba0bc391316aa558b3b4e8dd412045d2dc`、`result.auth_boundary=non_interactive_process_scoped_api_key`。成功dialog/terminalは0。
- evidence hashes: contract `444f69574cdc9543712235b345abe87f07ebc0986a36a241058df7730ec6f1ff`、lifecycle `055ba91650c473cba0af8df31120689bad96285e19813156c30ed40d4c68b897`、verified evidence `3777fd5e0158cdc4e472ce41b02df48624103b82fc2ea71350a398de0930539e`。完了後の該当pythonw/process markerは0、対象project fileは0。
- package evidence: installed DLL `235949f5a8fa51beace035d6940697707472e89e81783a3df6d34904e75af648`、installed v2 menu manifest `7f98af83dab53b65f67b0b2bedce1fb43518d8d74afd4e2641424eafbcc24344`。同一package transactionを更新し、二重package/二重登録なし。
- tests: native build + `ContractTest.exe` PASS、focused Python 1 PASS、full `py -3.12 -m unittest discover -s tests -v` 77/77 PASS。`.12` 自身のinstall/status/actual-menu/leaf受入は完了したため結果記録後にcloseし、rollback/開始前registry復元と独立実画面監査は後続 `sm-62a.13 -> sm-62a.14 -> sm-62a.6.7 -> sm-62a.15` のgateとして継続する。

### `sm-62a.13` / classic fallback と package transaction rollback

- resumed transaction: `sm-62a.12` の実 package install が保持した rollback pointから同じrollbackを再開した。sparse package削除後、transaction所有の `LocalMachine\\TrustedPeople` 証明書 `B789E1AEC1B4D82432F298C0EA20CD25281DBAC4` のsecure-desktop UACを一回だけユーザー承認し、途中でprocessが終了しても既存rollback pointを再利用して完走した。新規install/別transactionは開始していない。
- product fix: `native/windows-modern-context-menu/package.ps1` の `cleanup-certificate` は、transaction stateがmachine証明書作成済みでも既に削除済みなら `Test-Path` で検出し、再開時に不要な二回目の昇格を要求しない。実PowerShell focused testは存在しないmachine thumbprintから10秒以内にreturnしPASSした。
- semantic restoration: Directory export SHA-256 `e85e28f67cdf0e54e85032712a5af4a3c487439a5726a607752e2e18a22ad657`、Background export `e2ae9353434de03c1246ccba29510ac7e2025a5288d4c57c03b6ee71b507a0be` で、開始前baselineとbyte-equivalent。両rootはrootを含む8 key、開始前の既存classic内容へ復元した。
- actual Explorer: `C:\Projects\skill-magnet\.e2e-target\Modern 空白 & 日本語 (test)` のフォルダーをExplorerで右クリックし、「その他のオプションを確認」からclassic menuを実表示。`Skill Magnet` flyoutが一項目だけ存在し、modern package項目は0であることを画面確認した。leafは起動せず、menuをEscapeで閉じた。
- package/certificate/artifact cleanup（`.13` 実行時点の履歴）: 当時は対象thumbprint、`ContextMenu`、`.rollback`を0へ戻した。後続transactionも復元済みで、現在値は文頭のstrict residual readbackを正本とする。
- tests: focused rollback/certificate/package 3/3 PASS、full `py -3.12 -m unittest discover -s tests` 78/78 PASS (62.845s)、`git diff --check` PASS（既存LF/CRLF warningのみ）。source SHA-256: `package.ps1=378d2aa93a9058a1818d866a9028fd21d8110d612f051b9aaf87ecc08f0b0538`、`test_activation.py=cca8b403eb6e4bbddc90567a710d46dec39b97f1975b12d607fa5d1d33497885`。
- preservation/next: 実ユーザーproject、開始前classic登録、承認snapshot、既存dirty差分を保持し、commit/pushなし。結果を同Issueへ相互参照してclose後、`bd ready` の次の既存10分製品Issueだけをclaimする。

### `sm-62a.14` / Modern menu focused・E2E回帰

- native focused contract: `ContractTest.cpp` をroot title/flagsだけでなく、package root直下の全subcommandを順序つきで列挙する契約へ強化。`Skill: test-skill | Codex` と `Skill: test-skill | Claude` の2 leafだけを即時列挙し、余分・欠落・flattenをFAILにする。実native Release buildと `ContractTest.exe` は `SkillMagnet IExplorerCommand contract PASS`。
- package registration test: Python回帰が `AppxManifest.xml` を直接parseし、同一CLSID `13E2A9DD-4378-4F9D-A385-973C61B19E63` で `Directory` と `Directory\\Background` の2 contextだけが登録されることを固定した。
- focused E2E matrix: modern v2 package/skill/runtime hierarchy、両context特殊pathの単一argv、menu cancel副作用0、代表Codex `verified_applied` とsuccess UI 0、cleanup failure時verified 0、classic/package rollbackを同一focused runで7/7 PASS (6.405s)。実Explorer handoffは直前 `sm-62a.12` の両context `Package: PMO` 一項目/18 immediate leaf/代表Codex成功と、`sm-62a.13` のrollback後classic復元実画面を参照できる。
- regression: `py -3.12 -m unittest discover -s tests` 79/79 PASS (63.854s)、`git diff --check` PASS（既存LF/CRLF warningのみ）。SHA-256: `ContractTest.cpp=5b9a7dfb7f1adf7ac0304c9c80e996c4e900f3418ad6f2090a4e0f321117026d`、`AppxManifest.xml=4db2d9134b7aa26c20d3ba631cc6bb917eee0d101ad0b2fdb8b917687e035c3a`、`ContractTest.exe=744a0aca571d8005b6432280414469547c4b0066dcf140516e0357e66a730ae7`、`SkillMagnetCommand.dll=89387da903baef2916aa1005496f28e6692cd6388db1905893d3a247c0ec5e51`、`test_activation.py=41d37f4f23164ddd5fec0b5d0b9381c01becdec34d1ccea6ec08f7a40da09a2e`。
- cleanup/next（`.14` 実行時点の履歴）: 当時はpackage/external location/rollback point/一致process 0。後続transactionも復元済みで、現在値は文頭のstrict residual readbackを正本とする。実ユーザーproject、承認snapshot、既存dirty差分は保持し、commit/pushなし。

### `sm-62a.6.7` / 2026-08-23 current Explorer FAIL再読・登録gate

- canonical/current UI: handoff source task `01a02de6-721a-7d00-bad9-b8e34edc8817` から同じIssueだけを再開。`C:\Projects\skill-magnet\.e2e-target` の現Windows Explorer background通常右クリックを実観測し、`Package: PMO` は不在だった。strict OS readbackは `Get-AppxPackage SkillMagnet.ContextMenu=0` であり、COM列挙前のpackage未登録が現FAILの直接原因。Application Error/WERに `SkillMagnetCommand` / Explorer extension crashは検出されなかった。
- implementation diagnosis: product installはsigned sparse packageを `Add-AppxPackage -ExternalLocation` する前に `LocalMachine\TrustedPeople` へ署名証明書を登録する。最初のcanonical installではこのsecure-desktop UACがユーザー取消となり、install transactionはAppx、外部配置、rollback point、CurrentUser My/TrustedPeople、LocalMachine TrustedPeopleをすべて0へ自動復元した。
- bounded alternative: machine elevationを避ける `CurrentUser\TrustedPeople` 経路を実装してfocused 2/2 PASS後、同じ実machineでinstallを実行したが、Windows Appx verifierが `HRESULT 0x800B0109`（署名root未信頼）で拒否した。棄却したsource/test差分はその場で完全に戻し、再度 Appx=0、external=false、rollback=false、CU My=0、CU TrustedPeople=0、LM TrustedPeople=0を確認した。
- verification: full `py -3.12 -m unittest discover -s tests` は97/97 PASS (155.502s)。native `ContractTest.exe SkillMagnetCommand.dll` は `SkillMagnet IExplorerCommand contract PASS`、DLL SHA-256 `c592f616ef0b6dd3559f61a0e1ed3c4bbda12aed339b1169fd1df01dcf56ef5c`、menu SHA-256 `18fa35be385bb742f8c6044bd7e7c56666f3762c40c605c94d79d03992d69c47`。
- historical next state: この時点ではWindows UAC承認後の再実証を待っていた。後続のModern folder/background実証、代表Claude leaf送達、rollback証拠と直接ユーザー受入により、この旧再開条件は撤回した。

### `sm-62a.6.7` 直接ユーザー受入と現行Explorer再確認

- acceptance: 起点task `01a02c90-ccbb-77e2-a9ed-20d4f443c63d` でユーザーが「skill magnet出来上がってるやん！！」と直接評価した。これは以前のユーザー実機FAILより新しい受入証拠としてcanonical commentとmetadataへ帰属させた。
- current Explorer: 実Explorer Backgroundのclassic fallbackで `Skill Magnet -> Pack: codex-pmo-skills -> Skill: codex-auth-boundary-selection -> Codex / Claude` の階層と、対象AIが曖昧でない二つのleafを実画面確認した。既存のModern folder/background・9 skills × 2 runtimes証拠は上記field evidenceとして保持する。
- target delivery: 代表Claude leafを実選択すると認証済みChromeのClaude conversation windowへ遷移した。既存field evidenceのcontract `3c098122fea84c2999cacd94c9b76d85`、attempt `f9896ebd3dc84e8ebad81e0cdb2b0432`、選択skill instructions＋対象内容の単一prompt送達と突合した。Computer UseはURLを十分な確度で判定できず安全停止したため、それ以降の入力・送信はしていない。
- final closed state: Appx 0、owned certificate/process 0、classic fallback Directory/Background各8 keys、`ContextMenu`/`ContextMenu.rollback`なし、target child 0。開始前registry exportと最終rollback後exportはbyte一致し、新transaction・二重登録は開始していない。
- canonical: 古いWeb Codex/UAC metadataを撤回し、`.6.7`をclosedへ更新した。次は既存の独立監査 `.15` のみ。

### `sm-62a.15` 独立監査dispatch

- existing auditor: 既存監査task `01a027f4-816d-7ec0-aa89-0903f0665b63` へ同じ`.15`をdispatchした。新規Issue・新規監査taskは作成していない。
- first-marker readback: taskはactiveになったが、最初のassistant/tool/command marker前にCodex利用上限で`systemError`となった。`latestToolMarkerId=null`で、監査実行・製品変更は0。
- canonical: `.15`は`blocked`。単一再開条件は、既存監査taskの利用枠が2026-08-30 01:01 JST以降に利用可能になり、同じIssueで最初のstrict-readonly command markerを出すこと。

今後の実行Issueは、次の順を満たすまでcloseしません。

1. Issue scope内の変更と局所テストを完了する。
2. この結果MDへIssue ID、変更結果、テストcommand/result、artifactまたは実機証拠、cleanup状態、次のready/blockedを追記する。
3. `git diff --check` を通す。
4. Beads commentからこの結果MDの該当sectionを参照し、結果MDからもIssue IDを参照する。
5. その後にだけIssueをcloseする。
