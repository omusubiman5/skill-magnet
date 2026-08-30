# Skill Magnet API課金なし Desktop handoff 実装計画

日付: 2026-08-30  
対象: `C:\Projects\skill-magnet`  
方針: Codex/Claude API、API key、従量課金実行を製品経路で使用しない。

## 目的

Skill Magnetを「選択したskill packのINDEXと全SKILL.mdを一つの強制力のあるpromptへまとめ、利用者の既存Codex Desktop利用枠で新規taskを開始するアプリ」として完成させる。Skill MagnetはAI回答の完了を自己申告JSONで検証せず、OSが新規task handoffを受理した事実だけを記録する。

## 確定仕様

1. 選択単位は個別skillではなくpackとする。
2. INDEXとpack内全SKILL.mdをcontract専用の期限付きmaterializationへ固定し、pathとSHA-256をpromptへ含める。
3. Codexへ全ファイルの全文読了を命令し、実際の依頼に該当するskillを最低1件必ず適用させる。
4. `depends-on`は依存先を必須併用し、必要な`composes-with`を一つの実行方法へ統合し、`contrasts-with`を同時採用しない。
5. skillの説明、準備確認、実行可否の説明だけで終了することを禁止し、実際の依頼へ直接答える自然文成果を要求する。
6. Codex Desktopの成功状態は`desktop_handoff_ready`だけとする。回答完了、skill適用完了、`verified_completed`をSkill Magnet側から主張しない。
7. completion receipt、callback command、`activation-complete`、Desktop用output schemaを撤去する。
8. OpenAI Responses API、Anthropic API、API key、従量課金経路を製品コード・設定・promptへ追加しない。
9. Claudeも既存Web利用枠への新規conversation handoffに限定し、API実行へfallbackしない。

## 実装対象

### `src/skill_magnet/activation.py`

- Desktop receipt directory、生成、検証、lock、expiry cleanupを削除する。
- Desktop promptからreceipt schema、output path、callback commandを削除する。
- pack読了、skill適用必須、関係適用、説明だけで終了禁止を明文化する。
- handoff失敗時はmaterializationだけを安全に回収する。
- `desktop_handoff_ready`を非terminalの引渡し証拠として保持する。

### `src/skill_magnet/cli.py`

- `activation-complete` commandを削除する。
- Desktop handoff出力は`verified_completed`を返さず、`handoff_completed: true`と`answer_completion_claimed: false`を返す。
- API keyや有料APIを要求するcommandを追加しない。

### `src/skill_magnet/ui.py`

- OS protocol handoff成功時だけ「新規taskへ渡した」と表示する。
- 「処理完了」「skill適用検証済み」など回答完了を示す表示を禁止する。

### policy・文書

- `policy/product-policy.json`、`README.md`、`docs/mvp-redesign.md`をhandoff専用境界へ統一する。
- receipt由来の完成条件を削除する。
- API料金を消費しないことと、既存Desktop/Web利用枠を使うことを明記する。

### tests

- promptにINDEX、全SKILL.md、actual request、skill適用必須、関係規則、説明だけ禁止が含まれることを検査する。
- promptに`activation-complete`、receipt、output schema、API key要求が含まれないことを検査する。
- Desktop handoffが`desktop_handoff_ready`を返し、回答完了を主張しないことを検査する。
- launch失敗、期限切れmaterialization、同時操作、install/update/rollback/uninstall後の残留ゼロを維持する。
- Windows/macOSの通常製品経路と配布wheel gateを再実行する。

## 受入条件

- 製品コードからDesktop completion receiptと`activation-complete`が到達不能ではなく完全に削除されている。
- Codex Desktop promptがpack全体を一つのskill集合として扱い、最低1skillの実適用を必須にする。
- Skill Magnetはhandoff成功とAI回答完了を混同しない。
- 製品経路にOpenAI/Anthropic API keyまたは従量課金API呼出しがない。
- 全ローカルtest、standalone wheel再現性gate、Windows/macOS CI、Windows実機statusがPASSする。
- 実装報告書に変更、test数、artifact hash、CI、残存制約を記録する。

## リリース判定

上記受入条件がすべてPASSした場合、Skill Magnet本体は「API課金なしのDesktop/Web handoffアプリ」としてrelease candidateにできる。Codex Desktop上で生成される回答の品質はprompt契約と利用者受入で評価し、Skill Magnetが機械検証済みと偽装しない。
