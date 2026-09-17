import Foundation

// Stage 4's tag handling. Two properties matter, and they are not the same one:
//
//   `normalize` decides whether a tag is usable at all, and produces the string
//   handed to `Locale.Language`.
//
//   `modelScope` decides whether two tags are the *same model*, which is what
//   answers "is this pair worth a session" and "is this the same language, so
//   don't translate it". Getting this wrong in either direction is expensive:
//   too coarse and `zh-Hans` to `zh-Hant` silently no-ops, too fine and every
//   `pt-BR` reader mounts a second session for `pt-PT` text.

@MainActor
func runLanguageNormalizerTests(_ run: TestRun) async {
  run.suite("LanguageNormalizer")

  await run.test("collapses case, separator and region spellings onto one model") {
    for raw in ["fr", "FR", "fr-FR", "fr_fr", " fr-fr ", "fr-CA"] {
      guard case .language(let tag) = LanguageNormalizer.normalize(raw) else {
        run.expect(false, "\(raw) should normalize to a language")
        continue
      }
      run.expectEqual(LanguageNormalizer.modelScope(tag), "fr", "\(raw) shares the French model")
    }
  }

  await run.test("keeps the region in the tag it hands to Apple") {
    // Dropping it would be wrong in the other direction: `pt-BR` is the right
    // thing to *ask* for even though it shares a model with `pt-PT`.
    run.expectEqual(LanguageNormalizer.normalize("pt_br"), .language("pt-BR"), "pt_br")
    run.expectEqual(LanguageNormalizer.normalize("en-gb"), .language("en-GB"), "en-gb")
  }

  await run.test("treats every spelling of unspecified as automatic detection") {
    let unspecified: [String?] = [nil, "", "   ", "auto", "AUTO", "und", "unknown", "zxx", "mul"]
    for raw in unspecified {
      run.expectEqual(
        LanguageNormalizer.normalize(raw),
        .automatic,
        "\(raw ?? "nil") means auto-detect"
      )
    }
  }

  await run.test("rejects what is not a language tag") {
    for raw in ["e", "1", "f1", "123", "!!", "toolongsubtag"] {
      run.expectEqual(
        LanguageNormalizer.normalize(raw),
        .invalid,
        "\(raw) is not a language"
      )
    }
  }

  await run.test("tolerates a separator the caller left dangling") {
    // Neither of these is a rejection, and making one would be a regression
    // rather than a tightening.
    //
    // A tag that is nothing but separators has no primary subtag to be wrong
    // about, so it says the same thing "" says — auto-detect. Rejecting it
    // would fail a request that today succeeds by detecting the language.
    run.expectEqual(LanguageNormalizer.normalize("-"), .automatic, "- is unspecified")
    // `en_` is `en` with an empty region slot. The region is dropped because it
    // is absent, not because the tag is malformed.
    run.expectEqual(LanguageNormalizer.normalize("en_"), .language("en"), "en_ is en")
  }

  await run.test("moves Chinese script out of the region slot") {
    // `zh-CN` is the tag every server in the world sends and the one Apple
    // treats as unsupported, because the script is what selects the model.
    run.expectEqual(LanguageNormalizer.normalize("zh-CN"), .language("zh-Hans-CN"), "zh-CN")
    run.expectEqual(LanguageNormalizer.normalize("zh-TW"), .language("zh-Hant-TW"), "zh-TW")
    run.expectEqual(LanguageNormalizer.normalize("zh-HK"), .language("zh-Hant-HK"), "zh-HK")
    run.expectEqual(LanguageNormalizer.normalize("zh-hans"), .language("zh-Hans"), "zh-hans")
  }

  await run.test("keeps the two written Chinese languages apart") {
    // The counterpart to the test above: if `modelScope` dropped the script,
    // Simplified to Traditional would resolve as `same_language` and the
    // Translate button would do nothing at all.
    run.expectEqual(LanguageNormalizer.modelScope("zh-Hans-CN"), "zh-Hans", "simplified scope")
    run.expectEqual(LanguageNormalizer.modelScope("zh-Hant-TW"), "zh-Hant", "traditional scope")
    run.expect(
      LanguageNormalizer.modelScope("zh-Hans-CN") != LanguageNormalizer.modelScope("zh-Hant-TW"),
      "Simplified and Traditional are not the same model"
    )
  }

  await run.test("canonicalises the deprecated codes still in stored preferences") {
    let pairs = [("iw", "he"), ("in", "id"), ("ji", "yi"), ("no", "nb"), ("sh", "sr"), ("tl", "fil")]
    for (legacy, modern) in pairs {
      run.expectEqual(
        LanguageNormalizer.normalize(legacy),
        .language(modern),
        "\(legacy) is stored for \(modern)"
      )
    }
    // `mo` is both a deprecated Romanian code and Macau's region code. The
    // region mapping must not reach into the primary subtag.
    run.expectEqual(LanguageNormalizer.normalize("mo"), .language("ro"), "mo as a language")
    run.expectEqual(LanguageNormalizer.normalize("zh-MO"), .language("zh-Hant-MO"), "MO as a region")
  }

  await run.test("passes Haitian Creole through unchanged") {
    // Named explicitly by the brief. Whether Apple ships a model for `ht` is the
    // device's answer to give, and it can only give it if the tag survives to be
    // asked — a normalizer that mangled `ht` would report an unsupported pair
    // that was never actually queried.
    run.expectEqual(LanguageNormalizer.normalize("ht"), .language("ht"), "ht")
    run.expectEqual(LanguageNormalizer.normalize("ht-HT"), .language("ht-HT"), "ht-HT")
    run.expectEqual(LanguageNormalizer.modelScope("ht-HT"), "ht", "ht-HT model scope")
  }

  await run.test("puts a 3-digit UN region in the region slot, not the variants") {
    run.expectEqual(LanguageNormalizer.normalize("es-419"), .language("es-419"), "es-419")
    run.expectEqual(LanguageNormalizer.modelScope("es-419"), "es", "es-419 model scope")
  }
}

@MainActor
func runDigestTests(_ run: TestRun) async {
  run.suite("TranslationDigest")

  let pair = TranslationPairKey(source: "fr", target: "en")

  await run.test("is stable for the same text and pair within a process") {
    run.expectEqual(
      TranslationDigest.make(text: "bonjour", pair: pair),
      TranslationDigest.make(text: "bonjour", pair: pair),
      "same input, same digest"
    )
  }

  await run.test("separates text, source and target") {
    let base = TranslationDigest.make(text: "bonjour", pair: pair)
    run.expect(
      base != TranslationDigest.make(text: "bonsoir", pair: pair),
      "different text is a different job"
    )
    run.expect(
      base != TranslationDigest.make(
        text: "bonjour",
        pair: TranslationPairKey(source: nil, target: "en")
      ),
      "auto-detect is not the same job as an explicit source"
    )
    run.expect(
      base != TranslationDigest.make(
        text: "bonjour",
        pair: TranslationPairKey(source: "fr", target: "es")
      ),
      "a different target is a different job"
    )
  }

  await run.test("does not carry the text it digests") {
    // Stage 6: no raw private text in a cache key, a log line or a metric. This
    // value is only ever used to collapse in-flight duplicates, but it is a
    // string that gets passed around, so it must not be reversible by reading.
    let secret = "mon numéro de carte est 4242"
    let digest = TranslationDigest.make(text: secret, pair: pair)
    run.expect(!digest.contains("4242"), "digest does not contain the digits")
    run.expect(!digest.lowercased().contains("carte"), "digest does not contain the words")
    run.expect(digest.count < secret.count, "digest is shorter than what it digests")
  }
}
