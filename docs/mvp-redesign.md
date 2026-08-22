# GitHub中心MVP再設計

## 規範

製品ポリシーの唯一の規範的な定義は [`../policy/product-policy.json`](../policy/product-policy.json) です。

<!-- product-policy:begin -->
- GitHubのユーザー所有保管庫を唯一の正本とする。
- Skill Magnetはスキルの目的に沿って、必要なパックだけを明示選択して呼び出す。
- Codex/Claudeへの全件・常設・暗黙同期を既定にしない。
- 一時ローカル展開が技術的に必要な場合も、対象・理由・期限・cleanupを明示し、検証後に片付ける。
- 保管庫の版・来歴・承認を保持する。
- ローカル配置の成功を、スキルの読み込み成功または使用成功とみなさない。
- 選択したpackとversionをタスクへ明示し、読み込み証拠とスキル固有の適用証拠を検証する。
- 公式に確認できる経路または必要な証拠がない場合はfail-closedで停止し、保証外であることを明示する。
- 起動はユーザーの右クリックメニューからの明示選択を条件とし、自動提案・自動配布・自動有効化をしない。
- Windows ExplorerとmacOS Finderで同じ選択・確認・起動の意味と安全ポリシーを提供する。
<!-- product-policy:end -->

## 目的・成果物・完了条件

目的は、ユーザー所有GitHub保管庫の特定commitをユーザーが選び、対象Codexが選択skillを実際に読み、結果へ適用した証拠まで取得することです。

成果物は二つです。

1. Skill Magnet本体。共通コア、CLI、Windows/macOS UIアダプター、検証・証拠機構を含む。
2. Skill Magnetとは別の、ユーザー所有スキル保管庫。pack metadata、skill、version、skill固有acceptance checkを保持する。

両方を使ったend-to-end自動テストが、選択からCodexでの適用証拠まで合格した時だけ完成です。clone、展開、ローカル配置、候補表示、モデルの自己申告、旧syncテストの成功だけでは完成ではありません。WindowsまたはmacOSの片方だけでも完成ではありません。

## 最小アーキテクチャ

1. Registry/Resolver: ユーザー所有GitHub保管庫、pack ID、目的、完全なcommit SHA、承認、skill固有acceptance checkを検証する。
2. Selection UI: OSコンテキストメニューからアプリを開き、packを一つ明示選択させる。
3. Confirmation UI: 対象project/Codex、repository、commit、目的、検証方法を表示し、起動の明示確認を取る。
4. Launch contract: UIから共通CLIへ、選択内容、期限、nonce、確認時刻を機械可読形式で渡す。
5. Task envelope: pack ID、repository URL、commit SHA、skill ID、instruction digest、利用目的をCodexのタスク入力へ含める。
6. Runtime adapter: ローカル探索や暗黙選択に依存せず、選択packだけを正式なタスク入力へ送る。
7. Evidence verifier: 送達、読込識別、skill固有の適用を別々に検証する。
8. Journal/cleanup: source、承認、launch contract、証拠、一時物、期限を記録し、終了・失敗・中断・期限到来後に残留ゼロへ戻す。

GitHubのclone/cacheは正本ではなく、commitから再生成できる検証済み一時物です。

## UIとCLIの分離

共通コアはrepository、選択検証、launch contract、Codex task envelope、証拠判定、cleanupを所有します。OSアダプターはコンテキストメニュー登録、選択project pathの受け渡し、共通確認画面の起動だけを担当します。

| 層 | Windows | macOS | 共通条件 |
| --- | --- | --- | --- |
| 入口 | Explorerコンテキストメニュー | Finderコンテキストメニュー/Quick Action | ユーザーの右クリック操作が必要 |
| 選択 | Skill Magnet画面 | Skill Magnet画面 | pack一つを明示選択 |
| 確認 | 共通表示契約 | 共通表示契約 | 対象・版・目的・検証方法を確認 |
| 起動 | OSアダプターからCLI | OSアダプターからCLI | 有効なlaunch contractなしでは拒否 |

メニューの名称、階層、アイコンなどの見た目はOSごとの差を許容します。選択→確認→起動の意味、安全ポリシー、fail-closed判定は同一です。右クリックだけ、自動提案、ログイン、OS起動、repository更新をtriggerにして自動実行してはいけません。

## Codexの制約と正式ルート候補

Codexはrepositoryの `.agents/skills`、ユーザーの `$HOME/.agents/skills`、admin/system位置からskillを探索します。しかし配置は候補表示を可能にするだけで、正しいskillの選択・読込・適用を保証しません。また、Claude Codeの `--plugin-dir` と同等の任意ローカルskillをセッション限定追加する公式CLIオプションは確認できません。このため一時配置も常設配置もCodexの既定ルートにしません。

既定候補は次の通りです。

1. Resolverが固定commitの選択skillを検証し、pack ID、repository URL、commit SHA、skill ID、instruction digestを固定する。
2. ユーザーのタスクと検証済みinstructionをtask envelopeにし、公式の `codex exec PROMPT` 初期入力へ直接送る。
3. 送信したtask envelope bytesとdigestをjournalに記録する。`codex debug prompt-input` はmodel-visible promptを確認できますがexperimentalなので、補助preflight証拠としてのみ扱う。
4. `codex exec --json` のeventと、`--output-schema` で拘束したevidence envelopeを保存する。
5. evidence envelopeに受領したpack/version/skill/digestと適用規則を出力させる。これは読込識別の証拠であり、自己申告だけでは使用成功にしない。
6. スキル保管庫が提供する機械判定可能なacceptance checkを成果物または出力へ実行する。全checkが通った場合だけ `verified_applied` とする。
7. acceptance checkが未定義、実行不能、不一致、または必要な公式経路が使えない場合は `not_guaranteed` としてfail-closedで終了する。

これはモデル挙動の公式保証ではありません。公式に確認できる入力・記録機能とSkill Magnet側の検証を組み合わせ、誤った成功表示を防ぐ設計です。対話型Codexで同等の証拠回収ができない間、検証済みMVP経路は非対話 `codex exec` に限定します。

## Claude Code

Claude Codeは `--plugin-dir` でpluginをインストールせずセッションだけ読み込み、`/plugin-name:skill-name` で明示呼び出しできます。ただしClaude側も配置・読込だけを適用成功とせず、同じ三段階の証拠とacceptance checkを要求します。

## テスト可能な受入条件

- 起動時のactive packはゼロで、自動提案・自動配布・自動有効化がない。
- ExplorerとFinderの両アダプターで「右クリック→Skill Magnet→pack選択→対象/版/目的/検証方法の確認→起動」が成立する。
- 未確認、期限切れ、改変済み、再利用済みlaunch contractを共通CLIが拒否する。
- OSアダプターが選択外packを指定したり安全判定を迂回できない。
- WindowsとmacOSで同じ入力から同じlaunch contract意味論とfail-closed結果になるcontract testが通る。
- 片方のアダプターが未実装・skip・失敗なら製品完成判定が失敗する。
- ユーザー所有保管庫、完全commit SHA、来歴、承認、acceptance check不足を拒否する。
- task envelopeへ選択pack/version/skill/digestだけが入り、送達証拠が一致する。
- 配置、候補表示、モデル自己申告だけでは `verified_applied` にならない。
- Codexの読込識別とskill固有acceptance checkの両方が成功した時だけ `verified_applied` になる。
- 証拠不足、判定不能、不一致は `not_guaranteed` としてfail-closedになる。
- 正常終了、途中失敗、強制終了、期限切れ後に一時物とjournalの残留がない。
- Skill Magnet本体と独立スキル保管庫を使うend-to-endテストが全件成功する。

## 確認した公式資料

- [OpenAI: Build skills](https://learn.chatgpt.com/docs/build-skills) — Codexの明示・暗黙呼び出し、progressive disclosure、repository/user/admin/systemの探索位置。
- [OpenAI: Developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli) — `codex exec` の初期prompt、JSONL、output schema、ephemeral実行、experimentalなmodel-visible prompt診断。
- [Anthropic: Create plugins](https://code.claude.com/docs/en/plugins) — `--plugin-dir`、plugin名前空間、明示呼び出し。
- [Anthropic: Plugins reference](https://code.claude.com/docs/en/plugins-reference) — session-only pluginとinstalled pluginの違い。

2026-08-22時点のインストール済みCodex CLI 0.148.0のhelpも読み取り確認しました。任意ローカルskill/pluginをセッション限定追加するオプションは表示されません。
