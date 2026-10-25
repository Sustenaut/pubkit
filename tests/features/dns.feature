Feature: pubkit dns check compares a zone's nameservers and validates each one alone
  Every nameserver of a zone in pubkit.json's hosting block (the ones its
  providers are given there, the ones resolvers find, and the ones each of
  those lists) must give the same answers for every rrset the block
  describes. A signed zone's nameservers must each serve exactly the keys
  pubkit.json pins and validate alone, with the pinned key-signing keys as
  trust anchors, for every rrset and for a name that does not exist: what
  a resolver needs once the registrar has a DS record for each provider.
  The check only reads, through dig and delv.

  Background:
    Given pubkit.json has the hosting block the Terraform modules are tested with

  Scenario: A signed zone whose nameservers agree and each validate passes
    Given every nameserver of "example.net" serves it alike
    When I run pubkit "dns" "check" "example.net"
    Then it succeeds
    And it prints "agree at 4 nameservers: ns1.cloudflare.example., ns1.desec.io., ns2.cloudflare.example., ns2.desec.org."
    And it prints "every nameserver serves the 3 pinned keys and validates alone"
    And delv asked each of the 4 nameservers alone for a name that does not exist, with "example.net" as its root

  Scenario: An unsigned zone is compared, and nothing is validated
    Given every nameserver of "example.org" serves it alike
    When I run pubkit "dns" "check" "example.org"
    Then it succeeds
    And it prints "agree at 3 nameservers"
    And delv was not run

  Scenario: Every rrset the hosting block describes is compared
    Then the rrsets compared for "example.eu" are "@ NS, @ MX, @ TXT, _dmarc TXT, mbo0001._domainkey CNAME, mbo0002._domainkey CNAME, mbo0003._domainkey CNAME, mbo0004._domainkey CNAME, f67e29eb00a65673481c0f9731bc90e0c6f94fd3 TXT, @ A, @ AAAA, recipes CNAME, pub CNAME"

  Scenario: A nameserver with another answer fails the check
    Given every nameserver of "example.net" serves it alike
    And "ns2.desec.org." answers "example.net. A" with "192.0.2.9"
    When I run pubkit "dns" "check" "example.net"
    Then it fails
    And stderr mentions "example.net. A differs between nameservers"
    And stderr mentions "ns2.desec.org.: 192.0.2.9"

  Scenario: A nameserver a provider lists, but the configuration does not, is asked too
    Given every nameserver of "example.net" serves it alike
    And "ns1.desec.io." answers "example.net. NS" with "ns1.cloudflare.example., ns2.cloudflare.example., ns1.desec.io., ns2.desec.org., ns3.desec.example."
    When I run pubkit "dns" "check" "example.net"
    Then it fails
    And stderr mentions "ns3.desec.example. does not serve example.net"

  Scenario: A name in a nameserver's answer that is not a host name is never asked
    Given every nameserver of "example.net" serves it alike
    And "ns1.desec.io." answers "example.net. NS" with "ns1.cloudflare.example., ns2.cloudflare.example., ns1.desec.io., ns2.desec.org., -fevil."
    When I run pubkit "dns" "check" "example.net"
    Then it fails
    And stderr mentions "example.net. NS differs between nameservers"
    And stderr does not mention "-fevil. does not serve"

  Scenario: A nameserver that does not serve the zone fails the check
    Given every nameserver of "example.net" serves it alike
    And "ns2.cloudflare.example." does not serve the zone
    When I run pubkit "dns" "check" "example.net"
    Then it fails
    And stderr mentions "ns2.cloudflare.example. does not serve example.net (status REFUSED, not authoritative)"

  Scenario: A key pubkit.json does not pin fails the check
    Given every nameserver of "example.net" serves it alike
    And "ns1.cloudflare.example." also serves the key "257 3 13 c29tZXRoaW5nIGVsc2U="
    When I run pubkit "dns" "check" "example.net"
    Then it fails
    And stderr mentions "ns1.cloudflare.example. serves a key pubkit.json does not pin: 257 3 13 c29tZXRoaW5nIGVsc2U="

  Scenario: A provider that does not publish the other's keys fails the check
    Given every nameserver of "example.net" serves it alike
    And "ns1.desec.io." does not serve the cloudflare keys
    When I run pubkit "dns" "check" "example.net"
    Then it fails
    And stderr mentions "ns1.desec.io. does not serve the pinned key 256 3 13"

  Scenario: A nameserver whose answers do not validate against the pinned keys fails the check
    Given every nameserver of "example.net" serves it alike
    And "ns2.desec.org." signs with a key pubkit.json does not pin
    When I run pubkit "dns" "check" "example.net"
    Then it fails
    And stderr mentions "ns2.desec.org.: example.net. A does not validate against the pinned keys"
    And stderr mentions "ns2.desec.org.: a name that does not exist does not validate"

  Scenario: Another rrset can be compared with --record
    Given every nameserver of "example.org" serves it alike
    And "helium.ns.hetzner.de." answers "mail.example.org. MX" with "10 mail.example.net."
    When I run pubkit "dns" "check" "example.org" "--record" "mail/MX"
    Then it fails
    And stderr mentions "mail.example.org. MX differs between nameservers"

  Scenario Outline: A check pubkit cannot run stops it with a message
    Given every nameserver of "example.org" serves it alike
    When I run pubkit <args>
    Then it fails
    And stderr mentions "<message>"

    Examples:
      | args                                         | message                                          |
      | "dns" "check" "example.com"                  | has no zone example.com; it has example.eu       |
      | "dns" "check" "example.org" "--record" "mx"  | --record mx must be NAME/TYPE                    |
      | "dns" "check" "example.org" "--record=-f/etc/passwd/A"   | is not a DNS name pubkit dns check asks about |
      | "dns" "check" "example.org" "--record" "www/A;id" | is not a DNS record type                    |

  Scenario: The check needs dig
    Given every nameserver of "example.org" serves it alike
    And dig is not installed
    When I run pubkit "dns" "check" "example.org"
    Then it fails
    And stderr mentions "dns check needs dig, from BIND 9"

  Scenario: pubkit dns moves takes Cloudflare records from position keys to keys by value
    Given a Terraform state with Cloudflare records of a zone under position keys
    When I run pubkit dns moves on it
    Then it succeeds
    And it moves the NS, MX and apex TXT records to keys by their values, HCL-quoted
    And it moves 3 records, and none of the A, AAAA or CNAME records or those already keyed by value

  Scenario: pubkit dns moves says when there is nothing to move
    Given a Terraform state with no Cloudflare records under position keys
    When I run pubkit dns moves on it
    Then it succeeds
    And stderr mentions "no Cloudflare record of terraform/dns-zone is under a position key"

  Scenario: pubkit dns moves needs terraform show -json
    Given a Terraform state that is not JSON
    When I run pubkit dns moves on it
    Then it fails
    And stderr mentions "reads `terraform show -json` on stdin"
