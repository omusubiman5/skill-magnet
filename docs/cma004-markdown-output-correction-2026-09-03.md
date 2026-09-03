# CMA004 Markdown出力への訂正・対応報告

## 結論

CMA004を「CMA001の収集・抽出・日本語執筆・品質検査を維持し、Google Sheetsへの出力だけを1ニュース1 Markdownへ変更するSkill」へ差し替えた。誤って登録されていたPodcast MP3生成SkillはCMA004から除外した。CMA003は動画・音声バンドル生成であり、CMA004とは別工程である。

## 原因

`cma-004`というフォルダー名を要件より優先し、別リポジトリから復元されたPodcast実装を登録した。CMA001のMarkdown出力要件との入出力比較を公開前に行わなかった。

Library Managerにも、Skill登録時に`SKILL.md`と`acceptance.json`だけをコピーし、`references/`、`scripts/`、`agents/`を欠落させる問題があった。これでは説明だけが公開され、Skillを実行できない。

## 修正

- ローカル`cma-004`をCMA001 Markdown出力版へ差し替えた。
- `references/acquisition.md`、`references/schema.md`、Gmail抽出・Markdown出力スクリプトをCMA004へ含めた。
- Library ManagerがSkillフォルダー内の補助ファイルを再帰的にコピーし、manifest、GitHub公開、リモート照合へ含めるよう修正した。
- `custom-skills`が1 Skillだけの場合、pack purposeも更新後Skillのpurposeへ同期するよう修正した。
- 固定commitが変わった場合もWindowsメニューを再生成し、設定とメニューの不一致を残さないよう修正した。

## 検証

- Library Managerテスト35件: PASS
- 全テスト179件: PASS（環境依存1件skip）
- CMA004の構造・trigger・boundary検証: PASS
- 架空ニュース8件のMarkdown dry-run: `result=ready`、planned 8件
- GitHub PR作成、マージ、固定commitのリモートmanifest照合: PASS
- Windowsメニュー: `menu_contract_matches_config=true`、`usable_installed_state=true`
- CMA004の公開物: `SKILL.md`、`acceptance.json`、`agents/openai.yaml`、参照資料2件、実行スクリプト2件

公開途中の差分監査で、既存Codex Skill 9件のテスト資料18ファイルが削除対象になったことを検出した。CMA004とは無関係なため全18ファイルを復元し、次の同期でリモート照合まで完了した。

## 現在の公開状態

- Skill repository: `https://github.com/omusubiman5/codex-pmo-skills.git`
- 有効commit: `7af6c4f36b7183d9eaaaddf11c510769b632d2b8`
- 右クリック表示: `Skill: CMA004 — CMA001を1ニュース1Markdownで出力する`
- 旧Podcast版のローカル退避先: `C:\Users\HOMEA\.skill-magnet\backups\cma-004-podcast-before-md-20260903`
