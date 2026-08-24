# Skill Magnet 9個別skillテスト計画

## 位置づけと実行禁止ゲート

この文書は、Skill Magnetで9個のskillを一件ずつ選択・実行・証拠化するためのテスト計画の正本です。UX実装計画は [`windows-explorer-leaf-launch-plan.md`](windows-explorer-leaf-launch-plan.md)、実行結果は [`windows-explorer-leaf-launch-results.md`](windows-explorer-leaf-launch-results.md) に分離します。

- 親epic: `sm-62a`
- 本テスト計画の独立監査: `sm-62a.8`
- 9件の実行Issue: `sm-62a.5.7.1`〜`sm-62a.5.7.9`
- 9件の集約検証: `sm-62a.5.8`
- fixed-commit gate: `sm-62a.2`

`sm-62a.8` が独立監査PASSでcloseするまで、個別skill menuのUX実装、launch contract実装、各skill試験、Explorer実機試験を開始しません。監査PASS後も `sm-62a.2` のfixed-commit判断が完了するまでは、固定版を取得・実行しません。

## 共通前提

- pack ID: `codex-pmo-skills`
- GitHub owner: `omusubiman5`
- origin: `https://github.com/omusubiman5/codex-pmo-skills.git`
- config承認済みcommit: `c7747bba0bc391316aa558b3b4e8dd412045d2dc`
- 対象AI: CodexとClaude。各runtimeでschema/acceptance検証済みsessionが同じ実対話アプリへ引き継がれることを確認する。
- 選択契約: `selection_kind=skill`。selected skill IDは必ず一件。
- 右クリック経路: `右クリック → Skill Magnet → Pack: codex-pmo-skills → Skill: <skill-id> → Codex`
- 成功条件: contract、events、evidenceのpack ID、commit SHA、selected skill ID、instruction SHA-256、acceptance SHA-256が一致し、選択skillのassertionだけがPASSしたterminal evidenceを `verified_applied` とする。
- 失敗条件: SHA、ID、acceptance、runtime、source、approvalの不一致、別skill混入、証拠不足はfail-closed。`verified_applied` を作らず、一回のsanitized error UIと対応するnegative/rejected evidenceだけを許す。
- 成功時UI: 確認ダイアログやtoastは表示せず、選択した実Codex/Claudeの対話terminalを表示する。
- 対象project: 内容を変更しない隔離safe project。前後でproject、`.agents`、`.claude`、旧stateのhash/statusが不変であること。
- cleanup: 成功時は一時clone/stage/prompt/raw output/process markerがないこと。cleanup完了後にだけ `verified_applied` を確定する。

現在のlocal source `HEAD` は `225acbe63d0eecdc3617f443dcb495168a482ef4`、config固定版は `c7747bba...` で不一致です。したがって全テストの初期状態は `BLOCKED_FIXED_COMMIT` です。

## 証拠契約

各テストは、configured `state_dir` 配下の次をattempt IDで相互参照します。

- launch contract: `launch-contracts/<contract-id>.json`
- rejected event: `events/<attempt-id>-rejected.json`
- sanitized lifecycle event stream: `events/<contract-id>-lifecycle.jsonl`
- terminal event: lifecycle streamの最終行。`verified_applied`、`launch_failed`、`output_failed`、`acceptance_failed`、`cleanup_failed`、`interrupted` のいずれか一つで `terminal=true`
- success evidence: `evidence/<contract-id>-verified.json`
- failure evidence: `evidence/<contract-id>-not-guaranteed.json`
- 実行結果行: [`windows-explorer-leaf-launch-results.md#9-skills個別実行証拠行列`](windows-explorer-leaf-launch-results.md#9-skills個別実行証拠行列)
- Beads comment: 対応する `sm-62a.5.7.x` から本テストID、結果MD行、attempt ID、証拠pathを参照する。

事前拒否はcontractを作らないため `<attempt-id>-rejected.json` だけを使い、lifecycle streamを作りません。contract作成後はcontractに `attempt_id` と `contract_id`、lifecycle全行に同じ二つのID、terminal evidenceに同じ二つのIDと `terminal_event_id` を持たせて相互参照します。raw Codex event stream `evidence/<contract-id>-events.jsonl` は一時物であり、sanitized lifecycle streamとは別です。raw streamは検証・cleanup後に削除し、監査証拠として参照しません。

raw prompt、認証情報、raw model outputは証拠へ保存しません。各Issueは結果MDの自分の行に、実際に解決したattempt ID、contract path、sanitized lifecycle event path、terminal event ID、evidence pathを記録し、diff check、Beads相互参照が終わるまでcloseできません。

## 9 skills個別テスト

全digestは承認済みcommit `c7747bba0bc391316aa558b3b4e8dd412045d2dc` のblob bytesをSHA-256した値です。

| Test ID / Beads | 個別skill・固定digest | 右クリック選択 | 期待するskill固有assertionと成功 | 失敗時期待 | 証拠・結果行 |
| --- | --- | --- | --- | --- | --- |
| `SM-SK-001` / `sm-62a.5.7.1` | `codex-auth-boundary-selection`<br>instruction `90c8229b22400e8e08f31cd4b7d808fafe808bd35fa1d72235f40da7db1f072e`<br>acceptance `a993d08c8dfd9e4739bd9e6150772e2c45a67c40778d0051a35ceecdad27d92d` | Pack → Skill: `codex-auth-boundary-selection` → Codex | `result.auth_boundary == non_interactive_process_scoped_api_key`。この一件だけで `verified_applied` | 別認証方式、別skill assertion、digest不一致はnegative evidence。verified禁止 | contract/events/evidence、結果MD `SM-SK-001` 行 |
| `SM-SK-002` / `sm-62a.5.7.2` | `codex-bounded-subagents`<br>instruction `48094148e2ff65d16636a69b40e6ece8ce23361b308547b867d40d50a3a40c15`<br>acceptance `da393eec33b11f8d32dfb5fc68b744847f996e69d29cc9921708272d7eea1e47` | Pack → Skill: `codex-bounded-subagents` → Codex | `result.subagent_boundary == parallel_read_heavy_single_writer`。この一件だけで `verified_applied` | 無制限writer、別skill混入、digest不一致はfail-closed | contract/events/evidence、結果MD `SM-SK-002` 行 |
| `SM-SK-003` / `sm-62a.5.7.3` | `codex-ci-patch-handoff`<br>instruction `47bf82785f32845faefc6df75c8c0bec925e8b592b6d0eebd93264f833f404db`<br>acceptance `8b888999ad3cda87853e1f346a5386ac2adeca1230ab0cc00da70ab618e2a4b8` | Pack → Skill: `codex-ci-patch-handoff` → Codex | `result.ci_handoff == read_only_generation_binary_patch_separate_write_job`。この一件だけで `verified_applied` | CI内直接write、別skill assertion、digest不一致はfail-closed | contract/events/evidence、結果MD `SM-SK-003` 行 |
| `SM-SK-004` / `sm-62a.5.7.4` | `codex-context-entry-routing`<br>instruction `d2bf420e41bcd807761ddcdfb90f9f0580bbf362f6ea66290b28fb6ef7656220`<br>acceptance `414b8020359e9524c99b6f33fcb90e97149fb8fff5ec936576acff0bd8ccdea6` | Pack → Skill: `codex-context-entry-routing` → Codex | `result.context_entry == minimal_entry_with_task_contract`。この一件だけで `verified_applied` | 過剰context、task contract欠落、別skill混入はfail-closed | contract/events/evidence、結果MD `SM-SK-004` 行 |
| `SM-SK-005` / `sm-62a.5.7.5` | `codex-egress-surface-governance`<br>instruction `d28c14f3d3b8ad732a9b65860f6712026988ec01c13da90725bade110ce4bf54`<br>acceptance `928ab2a7112a4e3620fe373e170d64b2dc9490afcfe0822d66687bd404c178db` | Pack → Skill: `codex-egress-surface-governance` → Codex | `result.egress_control == per_surface_controls_no_bypass`。この一件だけで `verified_applied` | bypass許容、別skill assertion、digest不一致はfail-closed | contract/events/evidence、結果MD `SM-SK-005` 行 |
| `SM-SK-006` / `sm-62a.5.7.6` | `codex-exec-io-contract`<br>instruction `0ea30bdcfa9b75fc5a2914d112f026c00d8b9619416f0adf9b46da36d36072bc`<br>acceptance `4df5b00136f7456a67761f910cc93e31b57cf6b9fc7621b607dc2a3ac765862d` | Pack → Skill: `codex-exec-io-contract` → Codex | `result.exec_io == stdin_task_jsonl_events_schema_final_nonzero_fail`。この一件だけで `verified_applied` | schema/event欠落、失敗時zero扱い、別skill混入はfail-closed | contract/events/evidence、結果MD `SM-SK-006` 行 |
| `SM-SK-007` / `sm-62a.5.7.7` | `codex-execution-mode-routing`<br>instruction `173efc6e251be04266dcea9ab44bb2390337f6dec267881a1f5565fecd0754c0`<br>acceptance `f63c268df7677d9f051d47aec7476961e55694d50a7b738dbc02236cf838fea8` | Pack → Skill: `codex-execution-mode-routing` → Codex | `result.execution_mode == non_interactive_codex_exec`。この一件だけで `verified_applied` | 対話mode、別skill assertion、digest不一致はfail-closed | contract/events/evidence、結果MD `SM-SK-007` 行 |
| `SM-SK-008` / `sm-62a.5.7.8` | `codex-mcp-control-plane`<br>instruction `e48a4464a30a1f0c18dc81052353918b183b6d89f91f6b1f2569e0496a40ed53`<br>acceptance `4decbc6a5661eba06007372c0969a88c61afc15fa57f7471c11b63b6c8f1483e` | Pack → Skill: `codex-mcp-control-plane` → Codex | `result.mcp_control == required_read_only_allowlist_write_denied`。この一件だけで `verified_applied` | write許可、allowlist欠落、別skill混入はfail-closed | contract/events/evidence、結果MD `SM-SK-008` 行 |
| `SM-SK-009` / `sm-62a.5.7.9` | `codex-sandbox-approval-boundary`<br>instruction `e7a9def6a153946f6481412362ee7afaac99552ee7d7d9096b51a788887b0c05`<br>acceptance `b4df1deeebf2a0cfa03e93c19a59a90457f39279c51bf56d3ecc5003592e386d` | Pack → Skill: `codex-sandbox-approval-boundary` → Codex | `result.sandbox_boundary == read_only_no_midrun_approval`。この一件だけで `verified_applied` | mid-run approval依存、別skill assertion、digest不一致はfail-closed | contract/events/evidence、結果MD `SM-SK-009` 行 |

各テストは、他の8 skill ID、instruction、acceptance assertionがtask envelopeとevidenceに含まれないことも検証します。pack metadataに他skillが列挙されることと、モデル入力・適用対象に他skillが混入することを区別します。

## 統合テストケース

| Test ID | ケース | 操作・注入 | 期待結果 | 結果MD |
| --- | --- | --- | --- | --- |
| `SM-INT-001` | pack/skill menu完全性 | 両Explorer rootでpackを開く | 固定commitの9 skillが一度ずつ表示され、各skillにCodex/Claude leaf。旧pack-only実行leafなし | `SM-INT-001` 行 |
| `SM-INT-002` | Directory `%1` 特殊path | 空白・日本語・`& ( ) ' ! ^ # %` pathで任意の個別skill→Codex | project、pack、skill、runtimeが独立argvで完全一致 | `SM-INT-002` 行 |
| `SM-INT-003` | Background `%V` 特殊path | 背景右クリックから同条件 | `%V` とselected skillがcontractまで完全一致 | `SM-INT-003` 行 |
| `SM-INT-004` | skill ID/digest tamper | menu skill ID、instruction SHA、acceptance SHAを各一件改変 | AI起動前rejected、一回error、contract/verifiedなし | `SM-INT-004` 行 |
| `SM-INT-005` | cross-skill混入 | 選択外skill instruction/assertionをtask/evidenceへ注入 | acceptance failure、verifiedなし、選択skill IDを保持 | `SM-INT-005` 行 |
| `SM-INT-006` | Claude実runtime | 任意の個別skill→Claude | Codex fallbackなし。同じ固定skill/schema/acceptanceを`verified_applied`にし、同じsessionの実Claude PID/argvと対話terminalへ到達 | `SM-INT-006` 行 |
| `SM-INT-007` | source drift | source HEADをconfig expected commitと不一致にする | AI未起動、更新案内UI、rejected、verifiedなし | `SM-INT-007` 行 |
| `SM-INT-008` | cancel | Explorer menuを開くがCodex/Claude leafを選ばず閉じる | process、contract、event、evidence、UIを一切作らない | `SM-INT-008` 行 |
| `SM-INT-009` | 成功cleanup | 個別skill Codex成功 | 一時物/process marker削除後だけverifiedを原子的確定。成功UIなし | `SM-INT-009` 行 |
| `SM-INT-010` | cleanup失敗 | 一時物削除失敗を注入 | `cleanup_failed` negative evidence、verifiedなし、残留manifest/public recovery | `SM-INT-010` 行 |
| `SM-INT-011` | 強制中断 | Codex開始後にprocess中断し新Engine public entryを呼ぶ | `interrupted` 一回、negative evidence、marker/temp回収、verifiedなし | `SM-INT-011` 行 |
| `SM-INT-012` | stale/reinstall | skill構成またはdigest変更後、未reinstall menuから選択 | stale拒否。reinstall後だけ9 skill leaf/digest一致 | `SM-INT-012` 行 |
| `SM-INT-013` | pack全体leaf境界 | menuを列挙 | 現MVPでは暗黙pack全体leafなし。将来追加時は明示 `All skills (9)` と別contractが必要 | `SM-INT-013` 行 |

統合ケースのBeads対応は、`SM-INT-001`〜`003`・`012`・`013`=`sm-62a.3.6`〜`.3.7`、`004`・`005`・`007`=`sm-62a.5.3`〜`.5.4`（実Explorer driftは`.6.4`）、`006`=`sm-62a.5.6`、`008`=`sm-62a.5.9`、`009`=`sm-62a.5.2`、`010`=`sm-62a.5.4`、`011`=`sm-62a.5.5` です。各Issueは該当結果行をclose前に更新します。

## 実行順

1. `sm-62a.8` でこのテスト計画、UX計画、結果MD行、Beads依存を独立監査する。
2. 監査PASS後、個別skill menu/contractの実装Issueだけを依存順に進める。
3. `sm-62a.2` でfixed commitをユーザー決定し、owner/origin/commit/全9 digest/acceptanceを固定する。
4. 統合自動テストを先にPASSさせる。
5. `SM-SK-001`〜`009` を一件ずつ実行し、各結果MD行とBeads commentをclose前に更新する。
6. `sm-62a.5.8` で9件の一対一性、他skill混入なし、証拠完全性を集約検証する。
7. Explorer実機証拠と最終独立実装監査へ進む。
