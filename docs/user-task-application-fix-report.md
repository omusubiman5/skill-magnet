# 本来のユーザー依頼へのskill適用 修正対応報告

## 2026-08-28 起動失敗への対応

### 2026-08-28 00:25 継続状況

**未完了。** Explorer実画面スクリーンショットにより、旧Packメニューが別shell handlerから残存していることを確認した。個別skill実行、依頼入力、Terminal起動、skill実行結果は未到達。完了報告は禁止したまま原因調査を継続する。

### 現在の状態

**未完了。** 修正後のExplorer実起動とTerminal上のskill実行結果スクリーンショットが取得できていないため、対応完了とはしない。

### 実施した最小変更

1. Explorer leafを無表示の `pythonw.exe` から可視エラーを残す `python.exe` へ変更した。
2. クラシックメニューを4階層から `Skill Magnet → Skill: <id> → Codex / Claude` の3階層へ平坦化した。
3. pack ID、skill ID、commit、instruction digest、acceptance digestは各runtime commandのimmutable argvに維持した。
4. モダンメニュー再登録が90秒応答しなかったため中断し、クラシックメニューだけを再登録した。

### 非実動検証（完了証拠ではない）

- レジストリ: packキー0、skillキー9、runtime command 18。
- `python -m unittest tests.test_activation`: 61件実行、60件PASS、1件skip。
- `git diff --check`: whitespace errorなし。

### 実動検証

- 修正前の起動失敗スクリーンショット: 取得済み。
- 直接起動したSkill Magnet入力画面スクリーンショット: 取得済み。
- 修正後のExplorerメニュースクリーンショット: 未取得。
- 修正後のTerminal実行結果スクリーンショット: 未取得。
- 未取得理由: Computer Useが2回連続で `GetCursorPos failed: アクセスが拒否されました (0x80070005)`。

上記2枚を取得して内容を照合するまで、この報告の状態は未完了のままとする。

対応日: 2026-08-27（2026-08-28追記）

## 対応結果

「skillの自己検査だけを完了し、その後の本来の依頼は未検証」という経路を廃止した。Windows Explorerから起動する場合も、本来のユーザー依頼を取得・確認してから、選択skillと同じtask envelopeで実Codexへ送る。

## 実装変更

### Windows起動フロー

- 個別skill選択後に `Actual request` 入力を表示する。
- 空の依頼はcontract作成前に拒否する。
- 選択skill、runtime、固定commit、project、actual requestを確認後に実行する。
- 実行成功後だけ、検証済みの同一sessionを対話Codexへhandoffする。

### Task envelope

- `PURPOSE` をpackの固定説明ではなく、ユーザーが入力したactual requestにした。
- 「readiness exerciseやdemonstrationではなく、actual requestを完了する」ことを明示した。
- ユーザー向け成果物を `result.task_output` に返すよう要求した。

### 完了判定

次のすべてが揃う場合だけ `verified_completed` とする。

1. provenance、commit、instruction digest、challenge nonceが一致する。
2. 選択skillを具体的に適用したruleがある。
3. skill固有acceptanceが通る。
4. `result.task_output` が空でない文字列として存在する。
5. `completed_skill_ids` が選択skillと完全一致する。
6. `skill_execution_status=completed` である。
7. completion evidenceのactual-request SHA-256がcontractの依頼と一致する。
8. 対話handoffを要求した場合、同一sessionの実runtime PIDを確認できる。

## 追加した回帰条件

- Windows `context` が実行前にactual request入力処理を通る。
- actual requestから作成されたcontractを `interactive_handoff=True` で実行する。
- fake runtimeでも入力したpurposeが `task_output` へ保持される。
- `task_output` が欠落または空なら成功にしない。

## 旧実Codex出力（現行の起動・skill実行完了証拠として無効）

以下はCLI経路で保存された構造化出力であり、Explorer起動、Terminal表示、skill実行完了のスクリーンショットではない。今回の完了判定には使用しない。

実施したactual request:

> 認証境界の推奨を日本語で一文だけ作成してください。

実結果:

```json
{
  "status": "verified_completed",
  "task_output": "非対話型の自動処理では、APIキーを実行プロセスの存続期間だけ注入し、リポジトリ、ログ、成果物、共有ホームへ残さない認証境界を推奨します。",
  "skill_result": "non_interactive_process_scoped_api_key"
}
```

この結果では、ユーザー依頼そのものがtask envelopeへ入り、選択した `codex-auth-boundary-selection` のacceptanceと、依頼に対する具体的成果物の両方が同じ実行で得られた。

## 残課題

- Claudeは2026-08-27の実API試験で401 `OAuth access token has expired`。`claude auth login` による再認証後、同じactual-request E2Eを実施する必要がある。
- 現行runtimeはread-only sandboxである。ファイル変更を成果物とするskillを扱う場合は、書込み権限・承認・差分検証を別途設計し、暗黙に権限を広げない。

## 旧エビデンス（完了証拠として無効）

次のstate rootはactual-request適用までは証明するが、明示的なskill completion evidenceを持たないため、完了証拠として使用しない。

`C:\Projects\skill-magnet\.e2e-state\actual-request-evidence-20260827`

- contract ID: `7f238281fb92437485ba2d3d73bff2aa`
- attempt ID: `b8103ec8a0944fe0b5c90985d5776d0f`
- terminal event ID: `31233a47b43e4b118ba4adf28d5051b5`
- status: `verified_applied`（旧状態。完了とは認定しない）

保存ファイルとSHA-256:

| ファイル | SHA-256 |
|---|---|
| `launch-contracts/7f238281fb92437485ba2d3d73bff2aa.json` | `be61499ad1a26bcc9843b62d5fc62fe0fa282f5704ed4d141a0156813fcc1999` |
| `evidence/7f238281fb92437485ba2d3d73bff2aa-verified.json` | `34108277bdaa17c014156180fe92d2d8493f4e08bf94d86e4188a9c75b6e0155` |
| `events/7f238281fb92437485ba2d3d73bff2aa-lifecycle.jsonl` | `001e37bbd9979f9731c8aac9e8821a28f2c6644d5c2ccfb01998218d1f5cd16e` |

この旧証拠の `skill_specific_application_evidence` はacceptance定義のdigestであり、skill実行完了イベントではなかった。

### 証拠範囲

この永続E2Eが証明するのは、actual requestがcontractへ保持され、実Codexへ送達され、非空の成果物とskill固有acceptanceが同一実行で得られたことである。Explorer GUIで人が文字を入力・確認した操作自体は、純粋な自動E2Eではなく、CLI routingの回帰testで確認している。GUI操作まで含む最終受入は、通常の右クリック操作後に新しいcontract/evidence/lifecycle三点を同じ方法で照合する。

## Skill実行完了エビデンス

新基準による実Codex E2Eを次へ保存した。

`C:\Projects\skill-magnet\.e2e-state\skill-completion-evidence-20260827`

- contract ID: `6136fecaae024c57ab0f78ffe81ef7b9`
- attempt ID: `fdf9a2883230455a9179dda46b689da9`
- terminal event ID: `61267f1e2aee4ebba03fa1eed7dd15a2`
- terminal status: `verified_completed`
- completed skill: `codex-auth-boundary-selection`
- actual request SHA-256: `dc3c4763c75c4b0fb994136a484af29f02fcf523087865f6dd16dba80270e370`

| ファイル | SHA-256 |
|---|---|
| `launch-contracts/6136fecaae024c57ab0f78ffe81ef7b9.json` | `ab0255d25cf169997d12be773f3314e0872304b5c393b93971c8da86fc8cec99` |
| `evidence/6136fecaae024c57ab0f78ffe81ef7b9-verified.json` | `4b1ee6e6f7c4dc74f0643705883a662e630e69c9539ba98e8d8aa768e29a2f1e` |
| `events/6136fecaae024c57ab0f78ffe81ef7b9-lifecycle.jsonl` | `7ba2f7405fcea132adb19592dc9e9094b4a108c131d9531cd81271f2c1b042ff` |

独立再読取ではterminal status、完了skill完全一致、completion status、actual-request binding、非空成果物、skill acceptance、ID相互参照、nonceの8項目がすべて一致し、`all_pass=true` となった。
# 2026-08-27 再対応状況

状態: **未完了**

- Explorer 実操作で「クリックしても起動しない」を再現した。
- クラシックメニューをフォールバックとして再登録した。
- Explorer leaf を `pythonw.exe` から `python.exe` に変更し、無表示終了時にも起動・エラーを確認できるようにした。
- focused test `test_product_menu_has_all_nine_individual_skills_and_no_pack_leaf` は PASS。
- 再登録コマンドは終了コード 0。
- ただし再登録後の Explorer 実操作でも Terminal の skill 実行結果は未確認。よって完了とは報告しない。
