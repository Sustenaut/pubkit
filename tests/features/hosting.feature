Feature: pubkit.json's hosting block holds the providers and the zones
  terraform/hosting reads the hosting block: the providers a repository
  uses, each with its type and its own facts, and the zones, each naming
  the providers that serve its DNS, its email and its sites. Terraform
  ignores a setting it does not know, so pubkit sync checks the block first
  and stops on anything it cannot use.

  Scenario: The Terraform modules' test setup is a hosting block pubkit accepts
    Given the pubkit.json the Terraform modules are tested with
    Then pubkit accepts its hosting block

  Scenario: A repository without a hosting block has nothing to check
    Given the hosting settings '{"kind": "service"}'
    Then pubkit accepts its hosting block

  Scenario: Any name can be a provider of a type pubkit knows
    Given the hosting settings '{"hosting": {"providers": {"main-hetzner": {"type": "hetzner"}, "other-hetzner": {"type": "hetzner"}}, "zones": {"example.eu": {"dns": ["other-hetzner"]}}}}'
    Then pubkit accepts its hosting block

  Scenario Outline: A provider pubkit cannot use stops it with a message
    Given the hosting settings '{"hosting": {"providers": {"p": <provider>}}}'
    Then reading them stops with "<message>"

    Examples:
      | provider                                                          | message                                       |
      | {"type": "aws"}                                                   | providers.p.type must be one of hetzner       |
      | {"dns": {}}                                                       | providers.p.type must be one of               |
      | {"type": "hetzner", "account_id": "x"}                            | providers.p has no setting account_id         |
      | {"type": "hetzner", "dns": {"external_nameservers": ["a.b"]}}     | external_nameservers must be a list of host   |
      | {"type": "cloudflare", "account_id": "ABC"}                       | account_id must be the account's id           |
      | {"type": "cloudflare"}                                            | account_id must be the account's id           |
      | {"type": "google-cloud", "region": "europe-north1"}               | project must be the Google Cloud project      |
      | {"type": "google-cloud", "project": "p-1", "region": "Europe"}    | region must be a Google Cloud region          |
      | {"type": "google-cloud", "project": "p-1", "region": "r", "cloud_run": {"domain_target": "ghs"}} | domain_target must be the host |
      | {"type": "mailbox-org", "mx": ["mxext1.mailbox.org."]}            | mx must be a list of MX values                |
      | {"type": "mailbox-org", "mx": ["10 mxext1.mailbox.org"]}          | mx must be a list of MX values                |
      | {"type": "mailbox-org", "spf": "v=spf1 include:mailbox.org ~all"} | spf must be the SPF mechanisms                |
      | {"type": "mailbox-org", "dkim": {"MBO0001": "mbo0001.mailbox.org."}} | dkim must be an object mapping each selector |
      | {"type": "desec"}                                                 | external_nameservers must list the names deSEC |
      | {"type": "desec", "dns": {"external_nameservers": []}}            | external_nameservers must list the names deSEC |
      | {"type": "desec", "account_id": "x"}                              | providers.p has no setting account_id         |

  Scenario Outline: A zone pubkit cannot use stops it with a message
    Given the providers of the modules' test setup with the zone '<zone>'
    Then reading them stops with "<message>"

    Examples:
      | zone                                                                                    | message                                         |
      | {"dns": []}                                                                             | dns must list the providers                     |
      | {"dns": ["gcp"]}                                                                        | dns must list the providers                     |
      | {"dns": ["nowhere"]}                                                                    | dns must list the providers                     |
      | {"dns": ["hetzner"], "mx": []}                                                          | has no setting mx                               |
      | {"dns": ["hetzner"], "email": {"provider": "hetzner"}}                                  | email.provider must name a mail provider        |
      | {"dns": ["hetzner"], "email": {"provider": "mailbox", "verification": {"@": "x"}}}      | verification must be an object                  |
      | {"dns": ["hetzner"], "email": {"provider": "mailbox", "dmarc": {"policy": "block", "rua": "a@example.eu"}}} | dmarc.policy must be one of |
      | {"dns": ["hetzner"], "email": {"provider": "mailbox", "dmarc": {"policy": "none"}}}     | dmarc.rua must be the address                   |
      | {"dns": ["hetzner"], "apex_txt": [""]}                                                  | apex_txt must be a list of TXT values           |
      | {"dns": ["hetzner"], "sites": {"www": {"provider": "mailbox"}}}                         | provider must name a provider in hosting.providers that hosts sites |
      | {"dns": ["hetzner"], "sites": {"www": {"provider": "cloudflare", "kind": "server"}}}     | kind must be pages                              |
      | {"dns": ["hetzner"], "sites": {"www": {"provider": "hetzner", "kind": "server"}}}       | www.server must name a server                   |
      | {"dns": ["hetzner"], "sites": {"www": {"provider": "hetzner", "kind": "server", "server": "web", "project": "x"}}} | has no setting project |
      | {"dns": ["hetzner"], "sites": {"@": {"provider": "gcp", "kind": "cloud-run", "service": "s"}}} | cannot be at the apex                    |
      | {"dns": ["hetzner"], "sites": {"www": {"provider": "cloudflare", "kind": "pages"}}}      | project must be the Pages project's name        |
      | {"dns": ["hetzner"], "sites": {"www": {"provider": "cloudflare", "kind": "pages", "project": "p", "served_by": "server"}}} | standby must name a server |
      | {"dns": ["hetzner"], "sites": {"www": {"provider": "cloudflare", "kind": "pages", "project": "p", "served_by": "cdn"}}} | served_by must be one of |
      | {"dns": ["hetzner"], "sites": {"www": {"provider": "cloudflare", "kind": "pages", "project": "p", "subdomain": "p-2ab"}}} | subdomain must be the project's pages.dev name |
      | {"dns": ["hetzner"], "sites": {"www": {"provider": "cloudflare", "kind": "pages", "project": "p", "subdomain": "p.example.eu"}}} | subdomain must be the project's pages.dev name |
      | {"dns": ["hetzner"], "sites": {"WWW": {"provider": "hetzner", "kind": "server", "server": "web"}}} | must be named by the name relative     |
      | {"dns": ["hetzner"], "ttl": {"email": "1h"}}                                            | ttl.email must be a number of seconds           |
      | {"dns": ["hetzner"], "sites": {"www": {"provider": "hetzner", "kind": "server", "server": "web", "ttl": 1000000}}} | www.ttl must be a number of seconds |

  Scenario: A zone deSEC and Cloudflare serve can be signed by both
    Given the providers of the modules' test setup with the zone '{"dns": ["desec", "cloudflare"], "dnssec": {"keys": {"desec": ["257 3 13 AAAA"], "cloudflare": ["257 3 13 BBBB", "256 3 13 CCCC"]}}}'
    Then pubkit accepts its hosting block

  Scenario Outline: A dnssec block pubkit cannot use stops it with a message
    Given the providers of the modules' test setup with the zone '{"dns": <dns>, "dnssec": <dnssec>}'
    Then reading them stops with "<message>"

    Examples:
      | dns                       | dnssec                                                                                  | message                                          |
      | ["hetzner", "cloudflare"] | {"keys": {"hetzner": ["257 3 13 AAAA"], "cloudflare": ["257 3 13 AAAA"]}}              | cannot be set while hetzner serves the zone      |
      | ["desec", "cloudflare"]   | {"keys": {"desec": ["257 3 13 AAAA"]}}                                                  | keys must map each provider in the zone's dns    |
      | ["desec"]                 | {"keys": {"desec": ["257 3 13 AAAA"], "cloudflare": ["257 3 13 AAAA"]}}                | keys must map each provider in the zone's dns    |
      | ["desec"]                 | {"keys": {"desec": ["257 13 AAAA"]}}                                                    | keys.desec must be a list of DNSKEY values       |
      | ["desec"]                 | {"keys": {"desec": ["257 3 13 AA AA"]}}                                                 | keys.desec must be a list of DNSKEY values       |
      | ["desec"]                 | {"keys": {"desec": ["257 3 13 AAA*"]}}                                                  | keys.desec must be a list of DNSKEY values       |
      | ["desec"]                 | {"keys": {"desec": ["258 3 13 AAAA"]}}                                                  | keys.desec must be a list of DNSKEY values       |
      | ["desec"]                 | {"keys": {"desec": []}}                                                                 | keys.desec must be a list of DNSKEY values       |
      | ["desec"]                 | {"keys": {"desec": "257 3 13 AAAA"}}                                                    | keys.desec must be a list of DNSKEY values       |
      | ["desec", "cloudflare"]   | {"keys": {"desec": ["257 3 13 AAAA"], "cloudflare": ["256 3 13 AAAA"]}}                | keys.cloudflare must have a key-signing key      |
      | ["desec", "cloudflare"]   | {"keys": {"desec": ["257 3 13 AAAA"], "cloudflare": ["257 3 8 AAAA"]}}                 | keys must all use one algorithm                  |
      | ["desec"]                 | {"keys": {"desec": ["257 3 13 AAAA"]}, "ds": []}                                        | dnssec has no setting ds                         |

  Scenario Outline: A hosting block of the wrong shape stops it with a message
    Given the hosting settings '<config>'
    Then reading them stops with "<message>"

    Examples:
      | config                                           | message                                      |
      | {"hosting": []}                                  | hosting must be an object                    |
      | {"hosting": {"cloudflare": {}}}                  | hosting has no setting cloudflare            |
      | {"hosting": {"providers": []}}                   | must be objects, by name                     |
      | {"hosting": {"zones": {"Example.eu": {}}}}       | must be named by the domain                  |
      | {"hosting": {"ttl": {"sites": 30}}}              | ttl.sites must be a number of seconds        |
      | {"hosting": {"ttl": {"mx": 300}}}                | hosting.ttl has no setting mx                |

  Scenario: pubkit sync --check stops on a hosting block it cannot use
    Given a repository initialised as "utility"
    When the file "pubkit.json" reads "{"kind": "utility", "hosting": {"provider": {}}}"
    And I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "hosting has no setting provider"
