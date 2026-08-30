# Skill Magnet 製品完成 NO-GO 是正計画

日付: 2026-08-30  
対象: `C:\Projects\skill-magnet`  
基準: `policy/product-policy.json`、`docs/mvp-redesign.md`、`README.md`

## 判定

製品完成は **NO-GO**。Smart App Control 4551の修正、full MSIX、wheel単独性、Windows/macOS component CIの成功は、製品全体の完成を意味しない。

現行`Delivery Assurance` packへ更新後、ExplorerからCodex Desktopへ渡した自然文結果が再試験されていない。Finder CIは選択pathのprobeで終了し、pack選択、確認、launch contract、runtime deliveryを通らない。製品policyが要求する「両成果物を使う自動E2E」「全supported adapter」「skill readとINDEXに基づく適用部分集合の証拠」も未達である。

## 完成ゲート

1. Windows Explorerの現行`Delivery Assurance / 8f12af5…` leafから確認UIを開き、Codex Desktop新規taskへ渡す。
2. 新規taskがmaterialized INDEXと全SKILL.mdを読み、依頼に必要な複数skillをINDEX関係に従って適用し、具体的な自然文成果を返す。
3. Skill Magnet本体と独立packを使う自動E2EをCIへ追加し、handoffだけでなくcontract、全skill読込対象、適用部分集合、依頼hash、成果物、cleanupを検査する。
4. Finder lifecycleの`--release-probe`早期終了を廃止し、Windowsと同じselection、confirmation、contract、delivery意味論を最後まで通す。
5. Claudeの正式製品経路を一つに決める。session-only pluginを仕様とするなら`--plugin-dir`と明示skill呼出しを実装・実証し、Web/`--print`を混在させない。
6. acceptanceを特定CIシナリオの固定値から、actual requestに応じて選ばれた成果を検証できる契約へ改訂する。
7. README、Windows modern menu文書、Smart App Control報告書の対象範囲を規範policyと一致させる。
8. Windows/macOS CI、実Explorer、実Finder、実Codex Desktop、選択したClaude経路を再実行し、残留ゼロと証拠整合を鬼レビューする。

## 実装順

### Phase 1 — 虚偽完成防止

- 正本台帳をNO-GOとして固定し、旧packの成功を現行packへ流用しない。
- component PASS、ローカルRC、製品完成、公開配布を別判定にする。
- 完成ゲートの未達を自動検出するテストを追加する。

### Phase 2 — 関係・acceptance契約

- INDEX parserで`depends-on`、`composes-with`、`contrasts-with`を同じ正本から解釈する。
- 適用部分集合と関係選択理由を証拠へ含める。
- 固定equalsだけに依存しないrequest-aware acceptance schemaを定義する。

### Phase 3 — 両OS E2E

- Finder probeを完全semantic E2E adapterへ置き換える。
- Windows/Finder双方で同一のlaunch contractとfail-closed結果を検証する。
- product code pathを通らないfixtureを完成証拠に数えない。

### Phase 4 — 実runtime

- 現行packのExplorer→Codex Desktop自然文結果を取得する。
- INDEXにより複数skillが組み合わされたことを成果と証拠の両方で確認する。
- Claude経路を仕様と実装で一致させ、実runtime E2Eを追加する。

### Phase 5 — release

- 全test、wheel/MSIX/Finder lifecycle、実機E2E、cleanupを再実行する。
- 独立鬼レビューでP0〜P3を0にする。
- ローカルRCと公開配布を別々に判定し、公開用署名・notarization・publisher identityがなければ公開NO-GOを維持する。

## 完了禁止条件

次のいずれかが残る間は「完成」「リリース可能」と報告しない。

- `codex_desktop_result_status`がPASSでない。
- Finderがprobeで早期終了する。
- CIが両成果物を使うruntime E2Eを実行しない。
- 適用skill部分集合とINDEX関係の証拠がない。
- Claude仕様と実装が不一致。
- 正本文書間にsupported platform、menu shape、release scopeの矛盾がある。
- 鬼レビューにP0〜P3が一件でも残る。
