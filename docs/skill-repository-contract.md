# スキル保管庫契約

Skill Magnet本体とは別のユーザー所有GitHub repositoryだけが、skill contentの保管場所です。Skill Magnetはconfigでrepository URLと完全な40文字commit SHAを固定し、そのcommitのarchiveをprocess memory上で検証します。skill repositoryのclone、snapshot、INDEX、SKILL.md、acceptance.jsonをローカルディスクへ保存しません。

各packには、config上で次が必要です。

```json
{
  "id": "my-pack",
  "repo_url": "https://github.com/my-owner/my-skills.git",
  "expected_commit": "0123456789abcdef0123456789abcdef01234567",
  "skills": ["my-skill"],
  "approved_by": "user-identity",
  "approved_at": "2026-08-22T00:00:00+09:00",
  "purpose": "When and why this pack should be selected."
}
```

各skill directoryには `SKILL.md` と `acceptance.json` が必要です。

```text
my-skill/
├── SKILL.md
└── acceptance.json
```

repository rootの `INDEX.md` は任意です。pack内skill間に `depends-on`、`composes-with`、`contrasts-with` などの関係を定義する必要がある場合だけ置きます。INDEXが存在するpackでは固定commitのINDEXも検証・読了・適用対象とし、存在しないpackでは各SKILL.mdのtrigger/boundaryだけで適用集合を選びます。

MVPの `acceptance.json` は、Codexの構造化結果 `result` に対する完全一致条件を一つ以上定義します。

```json
{
  "version": 1,
  "assertions": [
    {
      "path": "result.decision",
      "equals": "bounded"
    }
  ]
}
```

現在のMVPで許可するpathは `result.<field>` の一階層だけです。複数skillのassertionが同じfieldへ異なる値を要求する場合はfail-closedです。acceptanceがない、空、壊れている、判定不能、またはCodex結果と一致しない場合、`verified_applied` は発行されません。

`acceptance.json` は単なる自己申告ではなく、skillの目的に固有で機械判定可能な結果を要求してください。ただし、LLMの内部状態を公式に保証するものではありません。これは回帰試験用のstructured verification adapterで使用するmetadataであり、Codex DesktopアプリまたはClaude Codeデスクトップアプリの利用者向けpromptへJSON出力形式を強制するものではありません。製品promptの成果形式は実際の依頼と適用skillに従います。
