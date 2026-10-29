# Changelog

## 1.0.0 (2026-10-29)


### Features

* add conditional formatting for Google Sheets output based on validation scores ([13023e7](https://github.com/agrc/naloxone-skid/commit/13023e7d3bbb9ef2f7600ad71dd76a98f593b6eb))
* add function to write DataFrame columns to Google Sheets ([96b4e1a](https://github.com/agrc/naloxone-skid/commit/96b4e1a8960f071901db27476881b7b1d5a7347c))
* add URL checking and geocoding functionality in main.py ([2c408c5](https://github.com/agrc/naloxone-skid/commit/2c408c52b1b00c9106c4ff800895eea7768a61e8))
* Log updated feature count after overwriting ([0ff44a1](https://github.com/agrc/naloxone-skid/commit/0ff44a1414c7a100d1e30c5144ec83817ba3cad6))
* update AGOL schema and implement data conformance function in main.py ([363c170](https://github.com/agrc/naloxone-skid/commit/363c17090b33c928642e02b4acdd6a4fba59530a))


### Bug Fixes

* correct entrypoint name in Dockerfile ([f41f161](https://github.com/agrc/naloxone-skid/commit/f41f161faf4eb2b22b150ad26cb3eae768f20be6))
* improve URL validation to handle malformed URLs and return appropriate error messages ([dcb1f21](https://github.com/agrc/naloxone-skid/commit/dcb1f21d55b10a9e50f8a220478c14e844f6b86b))
* include referrer in geocoder initialization for improved API call ([e9d9ac1](https://github.com/agrc/naloxone-skid/commit/e9d9ac191e8c8311d62c20a7239aae7789225cab))
* update AGOL schema by removing unused fields and adjust address concatenation logic ([54ef04c](https://github.com/agrc/naloxone-skid/commit/54ef04ccbf1703cf78501009e2e068e0e76b6a9e))
* update datetime usage and improve formatting in main.py ([c137c09](https://github.com/agrc/naloxone-skid/commit/c137c09acd6562566c87e16f0bd9679f20646275))
* update datetime usage to use UTC and improve formatting in main.py ([cdf095a](https://github.com/agrc/naloxone-skid/commit/cdf095aa82a29693adf67571cdfa9f48cabe44fa))
* update entrypoint name in Dockerfile ([4c4db7d](https://github.com/agrc/naloxone-skid/commit/4c4db7d4a5f42ca12b82f43f5dd6fd2bdcff3bf8))
* update geocoding API call to include Referer header and add new words to cSpell dictionary ([8859a7a](https://github.com/agrc/naloxone-skid/commit/8859a7ac42cc4f57bf8e5d1219d9935e53eb3fc3))
* update Google Sheets credentials for local and adjust URL field for link checking ([93662de](https://github.com/agrc/naloxone-skid/commit/93662de66fbb6d5cb44c2ad454e0d404c0f9c8ad))
* update Google Sheets loader to use Google Auth credentials ([a651c3d](https://github.com/agrc/naloxone-skid/commit/a651c3da8532aac3a38b971fa2395bc9965eeee9))
* update package name in version retrieval in __init__.py ([2b6e3c0](https://github.com/agrc/naloxone-skid/commit/2b6e3c06c116289c8d956fec74c3f39c8c31eccc))


### Dependencies

* update dependencies for ugrc-palletjack to version 6.1 ([a9f0252](https://github.com/agrc/naloxone-skid/commit/a9f0252a78ab22e0dea00ab8df5d7e5f7f3959b0))


### Documentation

* update readme and secrets template to include additional configuration fields ([619eb33](https://github.com/agrc/naloxone-skid/commit/619eb33cf12b1a166c11554bc0d89922705e2020))
* update README to reflect Conda environment setup instructions ([858bda0](https://github.com/agrc/naloxone-skid/commit/858bda0968ab9acbe942086e46c3e2eac7fa657b))

## [1.0.1](https://github.com/agrc/skid/compare/v1.0.0...v1.0.1) (2026-01-29)


### Dependencies

* **dev:** update functions-framework requirement ([e8bf430](https://github.com/agrc/skid/commit/e8bf4307d9abf7f200061abda6812ab6f6517366))
* **dev:** update pytest requirement from &lt;9,&gt;=7 to &gt;=7,&lt;10 ([5350132](https://github.com/agrc/skid/commit/5350132e4edbfa599ed7cd6f7b9ea898d46d2ad2))

## 1.0.0 (2025-07-30)


### Features

* gen2 trigger function ([a8a415a](https://github.com/agrc/skid/commit/a8a415a2d6f44b4be9b807498908b29d87aae976))


### Bug Fixes

* dep qualifiers ([0af573f](https://github.com/agrc/skid/commit/0af573f6315008d47db3e72861ba9bbcab63a7e4))


### Dependencies

* bump agrc-supervisor in the safe-dependencies group ([6278c89](https://github.com/agrc/skid/commit/6278c89b9ab9625551146258a0324fdb707f8594))
* bump ci deps 🌲 ([#19](https://github.com/agrc/skid/issues/19)) ([80b15c9](https://github.com/agrc/skid/commit/80b15c9ff5d2ddc30ac4716a85f31710ed1bc427))
* bump the major-dependencies group across 1 directory with 4 updates ([33bf047](https://github.com/agrc/skid/commit/33bf04702268f2fe759b2bdc91d2f4f39bf0d969))
* bump the major-dependencies group with 1 update ([391a0d5](https://github.com/agrc/skid/commit/391a0d5f6daaf16a51fcdcf5224adcb0b1272575))
* bump the major-dependencies group with 6 updates ([bdbb79c](https://github.com/agrc/skid/commit/bdbb79c576b15e7b840082766f6007ae91379b6e))
* bump to python 3.11 ([#20](https://github.com/agrc/skid/issues/20)) ([1aad176](https://github.com/agrc/skid/commit/1aad176f6ef28af6c3a4e15dd8a7694c6e923049))
* **dev:** bump the major-dependencies group with 4 updates ([6a73219](https://github.com/agrc/skid/commit/6a73219cc4aa7b1b1b204331a12e1b3ae63780b8))
* update supervisor ([14dc504](https://github.com/agrc/skid/commit/14dc504c164db958fe447d420f2f2cc745e107d6))
* update ugrc-palletjack requirement from &lt;5.2,&gt;=5.0 to &gt;=5.0,&lt;5.3 ([35f9e2b](https://github.com/agrc/skid/commit/35f9e2b7876ffcfc6a45f6f475ad3bb13852ee7a))
