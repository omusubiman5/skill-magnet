# 実依頼に `&#x20;` が残る問題の原因調査

日付: 2026-09-01

## 症状

Skill Magnetから作成した新規タスクの実依頼に、次のようなliteral文字列が残る。

```text
（**上記**重大な未完了事項  ）&#x20;
```

確認画面では空白に見える場合がある一方、Codex DesktopまたはClaudeへ渡されたpromptでは`&#x20;`が再び露出する。

## 原因

既存修正は表示境界だけを対象としていた。`src/skill_magnet/core.py`の`normalize_display_text`はnumeric U+0020参照を空白へ変換するが、関数の契約上、contract、hash、実行値には元文字列を保持する設計だった。

このため処理は次のように分断されていた。

1. UI確認文は`context_ui_text`を通るため、`&#x20;`が空白に見える。
2. 確定処理は`purpose.get()`のraw値を`ActivationEngine.plan`へ渡す。
3. `plan`は前後空白を除くだけで、literal `&#x20;`を保持する。
4. launch contract、依頼SHA-256、Desktop/Claude promptはraw値を使用する。

つまり、確認画面と実際に送達される依頼が同じcanonical文字列を共有していなかったことが直接原因である。

## 影響範囲

- Windows/macOSのコンテキストメニューUIから作成するCodex/Claude handoff
- `activation-plan` / `activation-launch`へ`--purpose`を渡すCLI経路
- launch contractの`purpose`、actual-request SHA-256、task prompt

一般HTML entityをdecodeする必要はない。`&lt;`、`&gt;`、`&amp;`までdecodeすると、利用者が入力したmarkup風文字列の意味を変え、表示上の注入面も広げる。この修正対象はsemicolonで閉じたnumeric U+0020参照だけである。

## 修正方針

依頼をcontractへ確定する`ActivationEngine.plan`をcanonicalization境界とする。

- `&#x20;`、`&#X20;`、zero paddingを含むhex表現、`&#32;`とzero paddingを半角空白へ変換する。
- 一般HTML entityと二重escapeされた`&amp;#x20;`は変更しない。
- canonical値をcontract、SHA-256、Codex/Claude promptのすべてで共有する。
- UI経路とCLI経路を同じ境界へ収束させる。

## 完了条件

1. entityを含む依頼から作ったcontractとhandoff promptにliteral `&#x20;`が残らない。
2. contractの依頼とSHA-256対象が同一のcanonical文字列になる。
3. `&lt;`および`&amp;#x20;`はliteralのまま保持される。
4. 通常の日本語・Markdown依頼は変更されない。
5. 関連回帰テストと全テストがPASSする。
