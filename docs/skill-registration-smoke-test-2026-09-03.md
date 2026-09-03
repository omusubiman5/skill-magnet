# 選択フォルダーからのスキル登録スモークテスト

## 今回発見した問題

実ファイル`C:\Users\HOMEA\.codex\skills\android-cli\SKILL.md`は、標準構成として必須のfrontmatter `name`／`description`とMarkdown本文を持つ。それにもかかわらずLibrary Managerは、本文中に英語の`trigger`／`boundary`または一部の固定日本語がないことを理由に登録を拒否した。

## 根本原因と前回修正の誤り

Library Managerが、Skillの適用範囲をLLMが判断するための意味情報と、保存時に機械検証できるファイル構造を混同していた。前回は日本語の固定語を正規表現へ追加しただけで、標準仕様にない独自必須条件を残した。このためCMA004という一例だけが通り、同じ原因を持つ`android-cli`で再発した。

## 修正

- `trigger`／`boundary`および日本語の固定語を探す登録拒否ロジックを撤去した。
- 機械検証は標準構造の`SKILL.md`、frontmatter `name`／`description`を基準にする。
- 適用対象や境界の意味評価は、実行時にSkill本文を読むLLMの選定処理へ残し、Library Managerの単純な文字列検索では代替しない。

## 実データ・スモーク範囲

1. 実`android-cli`フォルダーを空の隔離libraryへ登録する。
2. `acceptance.json`の自動生成とlibrary全体のvalidationを確認する。
3. 同一フォルダーの再登録が成功するno-opになることを確認する。
4. `C:\Users\HOMEA\.codex\skills`直下の非hiddenな全132候補を、一件ごとに空の隔離libraryへ登録する。
5. packを含む候補では全構成skillも数え、最初のエラーで停止せず全件の成否を集計する。
6. description欠落、secret候補、ID不一致、候補入替中断、backup後中断の失敗経路を回帰試験する。

実行結果は対応報告書へ記録する。
