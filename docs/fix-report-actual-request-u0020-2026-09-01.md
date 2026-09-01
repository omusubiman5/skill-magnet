# 実依頼の `&#x20;` 再露出 対応報告

日付: 2026-09-01

原因調査: [`root-cause-actual-request-u0020-2026-09-01.md`](root-cause-actual-request-u0020-2026-09-01.md)

## 結論

修正完了。確認UIだけでなく、launch contractへ依頼を確定する境界でnumeric U+0020参照を半角空白へcanonicalizeするよう変更した。これによりcontract、actual-request SHA-256、Codex Desktop prompt、Claude promptが同じ依頼文字列を共有し、次のliteral `&#x20;`はhandoff本文へ残らない。

```text
（**上記**重大な未完了事項  ）&#x20;
```

## 変更内容

### 1. 実依頼専用のcanonicalizationを追加

`src/skill_magnet/core.py`へ`normalize_actual_request`を追加した。対象はnumeric U+0020参照だけである。

- `&#x20;` / `&#X20;`
- zero paddingを含むhex表現
- `&#32;`とzero paddingを含むdecimal表現

`html.unescape`は使用せず、`&lt;`、`&gt;`、`&amp;`、二重escapeされた`&amp;#x20;`は変更しない。

### 2. contract作成前にcanonicalize

`src/skill_magnet/activation.py`の`ActivationEngine.plan`で、空欄判定とplan生成前に実依頼をcanonicalizeするよう変更した。

この一点を共通境界にしたため、次が同じcanonical値になる。

- UI確認後に作られるlaunch contract
- CLIの`activation-plan` / `activation-launch`
- actual-request SHA-256
- Codex Desktop task prompt
- Claude handoff prompt

### 3. 回帰テストを実送達境界まで拡張

`tests/test_activation.py`のentity-bearing requestテストを更新し、次を検証した。

1. contract内の`&#x20;`が空白へ変換される。
2. Desktop promptにcanonical依頼が含まれる。
3. promptに対象literal `）&#x20;`が残らない。
4. actual-request SHA-256がcanonical依頼から計算される。
5. `&lt;`と`&amp;#x20;`はdecodeされない。

### 4. 過去文書の境界変更を明示

2026-08-28の原因調査・対応報告へ、表示専用対策が現行仕様ではないことと本報告への参照を追記した。

## 検証結果

対象回帰テスト:

```text
Ran 3 tests in 1.644s
OK
```

全テスト:

```text
Ran 138 tests in 136.565s
OK (skipped=1)
```

skipは実行環境に`pythonw.exe`がない既知の環境依存テストであり、本修正の失敗ではない。

## 影響と互換性

- 通常の依頼文字列には変更なし。
- numeric U+0020参照を意図的にliteral文字列として送る用途では空白へ変換される。製品UIで観測された不具合を解消するための意図した仕様変更である。
- 一般HTML entityは保持するため、広範なHTML decodeによる文字列変質は発生しない。
- config、skill metadata、skill ID、GitHub固定commit、instruction digestの扱いは変更していない。

## 対応状態

コード修正、回帰テスト、全テスト、原因調査書、対応報告書まで完了した。実際のCodex Desktop/Claude画面での手動受入は、更新wheelを導入した環境で別途確認する。
