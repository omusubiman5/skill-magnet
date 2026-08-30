# Skill Magnet 製品完成 NO-GO 是正報告

日付: 2026-08-30  
計画: [`product-completion-remediation-plan-2026-08-30.md`](product-completion-remediation-plan-2026-08-30.md)

## 現在の判定

**NO-GO / 実装継続中**

Smart App Control 4551修正、full MSIX、wheel単独性、125テスト、Windows/macOS component CIはPASSしている。しかし、これは製品完成ゲートの一部にすぎない。

## 再監査で確認した未達

- 現行`Delivery Assurance / 8f12af5…`へ更新後のCodex Desktop自然文結果がない。
- `docs/windows-explorer-leaf-launch-results.md`は`NOT_RETESTED_AFTER_PACK_UPDATE`を明記している。
- Finder CIは`--release-probe`で選択pathを書いた直後に終了し、selection、confirmation、contract、runtimeを通らない。
- CIはSkill Magnet本体＋独立pack→実Desktop task→INDEX適用部分集合→成果という必須E2Eを実行しない。
- 現行packで複数skillをINDEXに従って組み合わせた実行証拠がない。
- Desktop handoffは正しく`verified_completed=false`だが、従ってpolicyの三段階成功条件も未達である。
- Claudeのsession-only plugin設計と、Web/`claude --print`実装が一致しない。
- acceptanceは特定headless CI回答の固定値で、一般のactual requestへ適応しない。
- README、Finder supported扱い、Windows modern menu文書、旧Smart App Control実機証拠のscopeに不整合がある。

## 今回実施した検証

- `python -m unittest discover -s tests -v`: 127件PASS、環境依存1件skip。
- 現行policy、MVP設計、Explorer正本台帳、Finder lifecycle、Claude adapter、INDEX parserを再読した。
- 独立鬼レビュー: P0 3件、P1 4件、P2 3件、P3 0件、NO-GO。

## 実装済みの是正

- Finder release lifecycleを、選択pathを書いて終了するprobeから、独立pack検証、launch contract、INDEX/全skill materialization、Codex Desktop delivery境界まで通すsemantic E2Eへ変更した。
- Finder semantic E2EはCodexとClaudeの両runtimeを別々に実行し、Codexは`codex://threads/new`、Claudeは`https://claude.ai/new`へ一つのpromptを渡すこと、handoffをcompletionへ昇格しないことを検査する。
- Windows/macOSのClaude製品経路をWeb Claude新規conversation prefillへ統一した。clipboard、既存conversation、常設plugin、headless `claude --print`へのproduct fallbackは廃止した。headless adapterは回帰試験用だけに限定した。
- INDEX parserとverificationへ`composes-with`を追加した。両端skillを同時適用する場合、両ID、`composes-with`、依頼固有の組合せ理由をapplied ruleへ要求する。
- package acceptanceの固定equalsを、同じJSON型の依頼固有値＋該当`result.*`を示すapplied ruleへ変更した。個別skill選択では従来どおり固定equalsを維持する。
- 実configと独立Delivery Assurance packを使い、Windows/macOS×Codex/Claudeのcontract、materialization、handoff境界を通す自動E2Eを追加した。
- README、MVP設計、Windows modern menu設計、Smart App Control報告書のscope矛盾を訂正した。

## 現行pack Desktop実機再試験

2026-08-30T05:39:30Z、現行`Delivery Assurance / 8f12af5…`、全9skill、複数skillを必要とするread-only依頼で`codex://threads/new`へhandoffした。contractは`612ac22908dd4d1aa70225728c631b71`、prompt SHA-256は`d0a3140b2fe0498d4e06303496f6bc6dc24baf594cc360e97aaa83af930594a9`。

handoff後、Codex task一覧に新規taskが現れず、自然文結果は取得できなかった。したがって結果は`desktop_handoff_ready`、`desktop_result_verification=not_available`、`verified_completed=false`のままであり、成功証拠へ数えない。証拠は[`test-evidence/delivery-assurance-desktop-20260830/handoff-log.json`](test-evidence/delivery-assurance-desktop-20260830/handoff-log.json)に保存した。

## 次の更新条件

この報告書は各Phaseの修正、追加test、実機証拠、CI URL、artifact hash、残留検査を実施するたびに更新する。現時点では現行packの実Desktop自然文結果、実Finder UI、公開署名・notarizationが未達である。全完成ゲートがPASSし、独立鬼レビューがP0〜P3すべて0になるまでGOへ変更しない。
