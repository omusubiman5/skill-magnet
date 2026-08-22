# Skill Magnet

Skill Magnetは、ユーザー自身のGitHub保管庫でスキルを保存・版管理し、仕事に必要なスキルパックだけを選んでCodexまたはClaude Codeに渡すためのローカルCLIです。

## 現在の状態

GitHub中心の手動activation経路を実装中です。共通コア、期限付きlaunch contract、Codex task envelope、証拠検証、Explorer/Finderアダプターは実装済みですが、登録中の実スキル保管庫には必須の承認情報とskill固有 `acceptance.json` がまだありません。このため実packのactivationは意図どおりfail-closedになります。実ユーザーCodexでの適用検証とmacOS実機検証も未完了なので、製品完成とは扱いません。

旧MVPの `sync` は `~/.agents/skills` と `~/.claude/skills` への常設コピーを前提とし、現在の製品ポリシーに適合しません。CLIでも既定無効です。本番 `sync` は実施していません。

## 製品ポリシー

唯一の規範的な定義は [`policy/product-policy.json`](policy/product-policy.json) です。以下は、その `principles_ja` を読みやすく表示したものです。文書と定義の不一致は自動テストで拒否します。

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

このポリシーから、次の既定動作が決まります。

- 起動時に有効なパックはゼロです。
- ユーザーは必要な時にアプリでパックと対象ランタイムを明示選択します。自動提案、自動配布、自動有効化はしません。
- GitHub URL、完全なcommit SHA、承認記録を検証してから有効化します。
- 一時展開物には対象、理由、期限、cleanup方法を記録し、検証後または期限到来時に削除します。
- 全パックの一括配布、ユーザー領域への常設配置、バックグラウンドでの暗黙同期は既定機能にしません。
- ファイルのclone、展開、配置、候補表示だけでは「スキル使用成功」と表示しません。

## 想定する利用手順

再設計後のMVPは、次の流れを満たすものとして実装します。コマンド名と引数はまだ確定していません。

1. ユーザー所有GitHub保管庫から利用可能なパックと、その目的・版・承認状態を一覧する。
2. 必要な時にユーザーがアプリで、目的に合うパック一つとCodexまたはClaude Codeを明示選択する。
3. `dry-run` で取得元commit、対象、展開場所、期限、cleanup予定、競合を確認する。
4. 選択したpack ID、GitHub URL、commit SHA、skill ID、instruction digestをタスクへ明示注入する。
5. タスクへの送達、skillの読込識別、skill固有の結果への適用を別々に検証する。
6. セッション終了後に一時展開物をcleanupし、残留ゼロを確認する。

`dry-run` を通していない有効化は拒否する設計です。

## MVPの起動UI

WindowsではExplorer、macOSではFinderで対象projectを右クリックし、Skill Magnetのメニューを開きます。OS固有の見た目や登録方式は異なっても、操作の意味は共通です。

最初にPython 3.12以降の環境へSkill Magnetをinstallします。

```powershell
python -m pip install -e C:\Projects\skill-magnet
```

1. コンテキストメニューからSkill Magnetを開く。
2. 利用するスキルパックを一つ明示選択する。
3. 対象Codex、GitHub URL、commit SHA、利用目的、検証方法を確認する。
4. ユーザーが起動を確定する。
5. 共通CLIへ期限付きのlaunch contractを渡す。

メニュー表示だけではスキルを取得・配置・有効化しません。確認画面を完了するまで、共通コアは実行を拒否します。Explorer/Finderは薄いOSアダプターとし、保管庫検証、選択、証拠、fail-closed判定は共通コアに置きます。

OSメニューを明示的に登録・解除するCLIは次の通りです。登録だけではpackを有効化しません。

```powershell
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json install-context-menu --platform windows --confirm
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json uninstall-context-menu --platform windows --confirm
```

```bash
python -m skill_magnet --config /path/to/skill-magnet.json install-context-menu --platform macos --confirm
python -m skill_magnet --config /path/to/skill-magnet.json uninstall-context-menu --platform macos --confirm
```

実packを変更せず事前検証するには `activation-plan` を使います。

```powershell
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json activation-plan --platform windows --project C:\Projects\target --pack codex-pmo-skills --purpose "このタスクの目的"
```

保管庫契約は [`docs/skill-repository-contract.md`](docs/skill-repository-contract.md) を参照してください。

## ランタイム方式の現状

- Claude Code: 一時プラグインを作成し、`claude --plugin-dir <temporary-plugin>` でそのセッションだけ読み込む案を第一候補とします。スキルは `/pack-name:skill-name` で明示呼び出しできます。
- Codex: ローカルスキルの公式探索先は作業repository配下の `.agents/skills`、ユーザー領域の `$HOME/.agents/skills` などです。任意の一時directoryをセッション限定探索先にする公式CLIオプションは確認できていないため、ローカル一時配置を既定案にしません。

Codexは選択pack/versionと検証済みinstructionを正式なタスク入力へ直接含めます。ただし、モデルへの送達やモデル自身の「読みました」という応答だけでは適用成功にしません。skill固有の機械判定可能な検査が通らなければ `not_guaranteed` としてfail-closedで停止します。詳細は [`docs/mvp-redesign.md`](docs/mvp-redesign.md) にあります。

## 成果物と完了条件

成果物は、Skill Magnet本体と、そこから独立したユーザー所有のスキル保管庫です。MVPの目的は、保管庫の特定commitをユーザーが選択し、対象Codexがそのskillを実際に読み、結果へ適用した証拠まで取得することです。

この一連を両方の成果物を使ったend-to-end自動テストで合格した時だけ完成とします。ローカル配置、候補表示、旧syncテストの成功だけでは完成ではありません。

## テスト

現段階のテストは二種類あります。

- activation E2E: 独立Git保管庫、両OSの起動契約、task envelope、challenge nonce、読込識別、skill固有acceptance、fail-closedを実subprocess境界で検証します。
- 製品ポリシーテスト: 規範的定義が必須制約を保持し、READMEと設計文書の表示が一致することを検証します。
- 旧MVPテスト: 旧常設syncエンジンの安全性を回帰確認します。成功しても、再設計後MVPの完成を意味しません。

```powershell
$env:PYTHONPATH = "C:\Projects\skill-magnet\src"
python -m unittest discover -s C:\Projects\skill-magnet\tests -v
```

再設計後MVPの完了条件は、別保管庫の特定commit選択、task envelopeへの明示注入、送達証拠、challenge nonceを含む読込証拠、skill固有の適用証拠、期限とcleanup、失敗・中断後の残留ゼロを含むend-to-end自動テストがすべて成功し、実ユーザーCodexとWindows/macOS実機で確認されることです。現時点では未完成です。

GitHub ActionsはWindowsとmacOSの両jobを必須の同一テストsuiteとして定義しています。片方だけの成功を完成扱いにしません。

対象Codexそのものを通す明示的なruntime acceptanceは、書込み禁止sandboxの一時project・一時Git保管庫で実行します。これはモデル呼び出しを行うため、通常のunit test discoveryには暗黙に含めません。

```powershell
$env:PYTHONPATH = "C:\Projects\skill-magnet\src"
python C:\Projects\skill-magnet\integration\real_codex_acceptance.py
```

2026-08-22にWindows上の実Codex CLI 0.148.0でこのruntime acceptanceを実行し、challenge nonce、commit、承認、instruction digestの読込識別とskill固有assertionが一致して `verified_applied` になりました。macOS実機と登録中の実packは未検証なので、製品全体はまだ完成扱いにしません。
