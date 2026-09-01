# `（鬼レビュー対応）&#x20;` 表示の原因調査

> 2026-09-01追記: この文書の「表示だけを正規化しcontractはraw保持」という境界は、handoff本文での再露出が確認されたため変更した。現行原因と境界は[`root-cause-actual-request-u0020-2026-09-01.md`](root-cause-actual-request-u0020-2026-09-01.md)を参照すること。

日付: 2026-08-28  
対象: `C:\Projects\skill-magnet`  
状態: コード上の原因確定・回帰修正済み、実画面受入未実施

## 結論

`&#x20;` はSkill Magnetが生成または二重escapeした文字列ではない。確認できた処理連鎖の最初の混入地点は、Codex desktopが受け取ったuser messageの `input_text` である。`C:\Users\HOMEA\.codex\sessions\2026\08\28\rollout-2026-08-28T01-04-47-01a043f7-7bfa-7123-a92c-bbeac1120848.jsonl:730` に、受信時点で `（鬼レビュー対応）&#x20;` がliteralとして保存されている。直後の同ファイル `:731` の `UserMessage` も同じ値であり、Skill Magnetの変換処理を通った証拠はない。

ただしSkill Magnet側にも、入力済みactual requestとskill metadataを文字列formatのまま表示する防御不足があった。修正前の `src/skill_magnet/ui.py` の `context_ui_text` はformat結果をそのまま返し、`context_ui_confirmation` はactual requestをそのまま確認文へ埋めていた。`Pack.skill_display_name` と `Pack.skill_purpose` もmetadataをそのままmenu/UIへ返していた。そのため、外部からliteral `&#x20;` が入った場合にSkill Magnetの確認表示・menu表示でもそのまま露出した。

## 事実

1. 修正前のrepo全体、installed menu、ユーザーstateを対象に `鬼レビュー対応|&#x20;|&amp;#x20` を検索した結果、Skill Magnetのsource/config/stateには該当文字列がなかった。
2. installed範囲 `C:\Users\HOMEA\AppData\Local\SkillMagnet` と `C:\Users\HOMEA\.skill-magnet` にも該当文字列はなかった。
3. 成功比較元 `C:\Projects\news-obsidian-pipeline\doc\Astro設定導線分断原因調査.md:41-43` は、依頼文末尾の `&#x20;` をHTMLの半角空白文字参照と記録し、同projectの障害原因とは分離している。
4. Skill Magnetのactual requestは `src/skill_magnet/activation.py:403` でcontractへ入り、`src/skill_magnet/activation.py:490` で `PURPOSE` として送られる。これは証拠hashと実行内容を一致させるためのraw値であり、表示都合で変更してはならない。
5. 一般HTML decoder、`html.unescape`、HTML escape処理はSkill Magnet repoに存在しなかった。

## 再現条件

修正前は、`context_ui_confirmation("ja", details, "（鬼レビュー対応）&#x20;")` の戻り値に `&#x20;` が残った。skill metadataの `display_name` または `purpose` に同じ値を置いた場合も、Windows menu manifestと確認UIへliteralが渡った。

観測されたCodex message自体は、Skill Magnet repoの再現ではなくuser inputとして既にliteralを含む。入力前のclipboard、Web表示、または手入力のどこでHTML文字参照へ変わったかは保存証拠がなく不明である。

## 根本原因

根本原因は二つに分ける。

- 観測されたmessageの混入原因: Codex clientへ渡されたuser inputが既にHTML文字参照のliteralだった。Skill Magnetによる生成という仮説はrepo、installed manifest、state、session順序の証拠で棄却した。
- Skill Magnetの表示漏れ原因: 表示層が「実行値を変更しない」ことだけを実装し、表示専用の最小正規化を持っていなかった。このため、外部由来のU+0020数値参照を文字列として見せた。

## なぜtestが検出しなかったか

既存testのfixtureは日本語表示名、purpose、actual requestの通常文字列だけだった。`tests/test_activation.py` の既存confirmation testは「actual requestをそのまま含める」ことを確認していたが、表示値とcontract値を分離するケースを持たなかった。menu metadata testにもHTML文字参照がなく、一般decodeを拒否するsecurity回帰もなかった。

つまり、pass-throughの正しさは検証していたが、「raw contractは不変、human-facing表示だけU+0020を空白にする」という二層契約がtest定義に存在しなかった。

## 修正方針

`src/skill_magnet/core.py:23` に表示専用 `normalize_display_text` を追加し、semicolonで閉じたnumeric U+0020参照だけを半角空白へ変換する。対象は `&#x20;`、`&#X20;`、zero paddingを含むhex表現、`&#32;` とそのzero paddingに限定する。

一般的な `html.unescape` は使わない。`&lt;`、`&gt;`、`&amp;`、script文字列などはdecodeしない。関数は新しい文字列を返すだけで、config、内部skill ID、actual request、contract、hash、task envelopeを変更しない。

適用境界は次の二か所である。

- `src/skill_magnet/core.py:170-178`: skill表示名・用途をmenu/UIへ返す境界
- `src/skill_magnet/ui.py:96-100`: ローカライズ済みhuman-facing UI文字列の最終境界

## 非対象と未確認

- 既にCodexへ直接入力されたmessageはSkill Magnetから書き換えられない。
- 入力前のclipboardや外部HTMLの生成元は証拠不足で確定していない。
- Explorer/Tk実画面での表示確認はComputer Use禁止のため実施していない。自動test合格だけで実機完成とは扱わない。

## Installed反映の確認

2026-08-28 04:14 JSTに、更新前のinstalled状態をread-onlyで確認した。modern packageは既に `usable_installed_state=true`、classic/legacy registry root 4件は不存在、original rollbackは3 files・7,970 bytes・tree SHA-256 `fad5c98933a12396d0f0e78fe14b43457e4aceceea59be53bd86aecf4edc7e30`、active `ContextMenu.rollback.update` は不存在だった。

この事前条件の後、既存製品APIを次の1回だけ実行した。

```text
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json install-context-menu --platform windows --confirm
```

exit 0。transaction後のreadbackで次を確認した。

- package `SkillMagnet.ContextMenu_1.0.0.0_x64__byy1sc3mfzfz4` はregistered、identity/COM identity/2 contexts/command target/DLL/menu manifestが一致し、`usable_installed_state=true`。
- classic fallbackは `installed=false`。classic/legacy registry root 4件はすべて不存在。
- active `ContextMenu.rollback.update` は不存在。
- original rollbackは3 files・7,970 bytes・同じtree SHA-256で、内容不変。
- installed menuはSHA-256 `c5b53ea632844e8d713feace9d1d308a56e496f73cea2bda695c7fdbfa4b6a38`、v3・9 leaves。9件すべてのbootstrapが `C:\Projects\skill-magnet\src`、configが `C:\Projects\skill-magnet\skill-magnet.json` を参照する。Skill MagnetはPython sourceをinstalled directoryへ複製せず、この固定bootstrap参照からcurrent sourceを読む構造である。
- current source SHA-256は `core.py` が `d2e2d65ea988a5b8f61558c4b65e970ef9965e7f75c0b7d97b05e9952c94dd47`、`ui.py` が `19f08d1e87916471ded97a889b7244a55943de22cd2c54a2c5470ed28ada8f38`。installed bootstrapの参照先はこれらを含む同じsource rootである。
- installed menu内のliteral `&#x20;` は0件。

このinstalled反映はコード参照・package整合・排他状態を証明するが、Explorer/Tk実画面での文字表示は証明しない。Computer Use、GUI操作、Codex/Claude起動は実施していない。
