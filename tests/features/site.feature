Feature: pubkit site check reads a built site as robots and other sites do
  A public site's pages name their language, title and description, and
  carry an Open Graph card whose image is large enough for LinkedIn's large
  preview, unless they ask not to be indexed. robots.txt lets the link-preview bots in, even where it keeps
  search engines out. deploy-static runs the check before every deploy.
  Each rule has an ID, and pubkit.json can declare an exception to it.

  Background:
    Given a built site in "_site" with a complete page "index.html"

  Scenario: A complete site passes
    When I run pubkit "site" "check" "_site" "--url" "https://example.eu"
    Then it succeeds
    And it prints "1 page(s) meet the 6 site rules"

  Scenario: Without --url the pages are checked, and the images are not looked for
    Given the site's card is removed
    When I run pubkit "site" "check" "_site"
    Then it succeeds
    And it prints "give --url to check the og:image files too"

  Scenario: A page with no language, description or card fails, naming each rule
    Given the page "_site/about/index.html" has only a title
    When I run pubkit "site" "check" "_site" "--url" "https://example.eu"
    Then it fails
    And stderr mentions "WEB-PAGE-01 about/index.html: no <html lang>"
    And stderr mentions "WEB-PAGE-02 about/index.html: no <meta name="
    And stderr mentions "WEB-OG-01 about/index.html: no og:title, og:description, og:type, og:url, og:image"
    And stderr mentions "WEB-OG-03 about/index.html"
    And stderr does not mention "WEB-PAGE-02 index.html"

  Scenario: An og:image smaller than LinkedIn's large card fails
    Given the site's card is a 640×480 JPEG
    When I run pubkit "site" "check" "_site" "--url" "https://example.eu"
    Then it fails
    And stderr mentions "og:image https://example.eu/card.png is 640×480; make it at least 1200×627"
    And stderr mentions "og:image:width says 1200, but https://example.eu/card.png is 640"

  Scenario: An og:image that is not a PNG or JPEG fails
    Given the site's card is a WebP
    When I run pubkit "site" "check" "_site" "--url" "https://example.eu"
    Then it fails
    And stderr mentions "is not a PNG or JPEG, which LinkedIn needs"

  Scenario: An og:image missing from the site fails
    Given the site's card is removed
    When I run pubkit "site" "check" "_site" "--url" "https://example.eu"
    Then it fails
    And stderr mentions "WEB-OG-02 index.html: og:image https://example.eu/card.png is not in the site"

  Scenario: A robots.txt that keeps everyone out hides the card
    Given the site file "_site/robots.txt" holds "User-agent: *\nDisallow: /"
    When I run pubkit "site" "check" "_site" "--url" "https://example.eu"
    Then it fails
    And stderr mentions "WEB-ROBOTS-01 robots.txt keeps LinkedInBot from https://example.eu/, https://example.eu/card.png"
    And stderr mentions "robots.txt keeps facebookexternalhit from"

  Scenario: A robots.txt that keeps search engines out but lets preview bots in passes
    Given the site file "_site/robots.txt" holds "User-agent: LinkedInBot\nUser-agent: facebookexternalhit\nUser-agent: Twitterbot\nUser-agent: Slackbot\nAllow: /\n\nUser-agent: *\nDisallow: /"
    When I run pubkit "site" "check" "_site" "--url" "https://example.eu"
    Then it succeeds

  Scenario: Redirects and the 404 page are not checked
    Given the site file "_site/old/index.html" holds "<meta http-equiv=\"refresh\" content=\"0; url=/\">"
    And the site file "_site/404.html" holds "<title>Not found</title>"
    When I run pubkit "site" "check" "_site" "--url" "https://example.eu"
    Then it succeeds
    And it prints "1 page(s) meet"

  Scenario Outline: A page that asks not to be indexed needs no description or card
    Given the page "_site/status/index.html" has a language, a title and robots "<robots>"
    When I run pubkit "site" "check" "_site" "--url" "https://example.eu"
    Then it succeeds
    And it prints "2 page(s) meet"

    Examples:
      | robots            |
      | noindex           |
      | NOINDEX, NOFOLLOW |
      | nofollow,noindex  |
      | none              |

  Scenario: A page that asks not to be indexed still names its language and title
    Given the page "_site/status/index.html" has only a title and robots "noindex"
    And the site file "_site/robots.txt" holds "User-agent: *\nDisallow: /status/"
    When I run pubkit "site" "check" "_site" "--url" "https://example.eu"
    Then it fails
    And stderr mentions "WEB-PAGE-01 status/index.html: no <html lang>"
    And stderr does not mention "WEB-PAGE-02 status/index.html"
    And stderr does not mention "WEB-OG-01 status/index.html"
    And stderr does not mention "WEB-OG-03 status/index.html"
    And stderr does not mention "WEB-ROBOTS-01"

  Scenario: nofollow alone still asks for the card
    Given the page "_site/status/index.html" has a language, a title and robots "nofollow"
    When I run pubkit "site" "check" "_site" "--url" "https://example.eu"
    Then it fails
    And stderr mentions "WEB-PAGE-02 status/index.html: no <meta name="
    And stderr mentions "WEB-OG-01 status/index.html"

  Scenario: A declared exception waives its rule and is listed as open
    Given the page "_site/about/index.html" has only a title
    And pubkit.json declares exceptions for "WEB-PAGE-01, WEB-PAGE-02, WEB-OG-01, WEB-OG-03" until "2099-01-01"
    When I run pubkit "site" "check" "_site" "--url" "https://example.eu"
    Then it succeeds
    And it prints "open exception: WEB-OG-01 until 2099-01-01"
    And it prints "2 page(s) meet the 2 site rules"

  Scenario: An expired exception fails
    Given pubkit.json declares exceptions for "WEB-OG-03" until "2020-01-01"
    When I run pubkit "site" "check" "_site" "--url" "https://example.eu"
    Then it fails
    And stderr mentions "WEB-OG-03: the exception expired on 2020-01-01"

  Scenario: A site exception is not a Rust standard rule to pubkit conform
    Given pubkit.json declares exceptions for "WEB-OG-03" until "2099-01-01"
    Then pubkit conform reads no exception from it

  Scenario: A directory with no pages fails
    When I run pubkit "site" "check" "_site/about-nothing" "--url" "https://example.eu"
    Then it fails
    And stderr mentions "is not a directory; build the site first"
