Feature: How a walk reaches the cluster and the server
  The admin channel is ssh root@HOST, from --server or SUSTENAUT_SERVER:
  kubectl is `k3s kubectl` there, and a command runs there, its arguments
  one quoted line, what is secret on its standard input, every command over
  one SSH connection the walk keeps. The deploy channel
  is the server's read-only verb, one JSON line a call over one connection,
  in the unit's own namespace. A kubeconfig's kubectl is plain, and the plane
  is in-cluster. Here ssh is a stand-in that keeps what it was given.

  Scenario Outline: The channel comes from --kubectl, --server and the environment
    Given the walk is given <given>
    When I make the channels for the namespace "example"
    Then the channel is "<channel>"
    And a command <runs> on the server

    Examples:
      | given                                                        | channel | runs           |
      | --server "server.example.eu"                                 | admin   | runs           |
      | the environment's SUSTENAUT_SERVER "server.example.eu"       | admin   | runs           |
      | --kubectl "ssh -i key deploy@server.example.eu {unit} kubectl-read" | deploy  | never runs |
      | --kubectl "kubectl --context example"                        | plain   | never runs     |
      | nothing                                                      | plain   | never runs     |
      | the environment's PUBKIT_PLANE "1"                           | plane   | never runs     |

  Scenario: Over the admin channel kubectl's arguments are one quoted line, and a manifest comes on standard input
    Given the walk is given --server "server.example.eu"
    When I make the channels for the namespace "example"
    And kubectl applies a Secret manifest holding a value
    Then ssh was run as "ssh -o BatchMode=yes root@server.example.eu", over the walk's connection, and the line "k3s kubectl -n example apply -f -"
    And the value came on ssh's standard input, and in no argv

  Scenario: A command on the server is quoted as one line
    Given the walk is given --server "server.example.eu"
    When I make the channels for the namespace "example"
    And the server runs "python3 -I - --name 'example.eu team sites'" with a script on its standard input
    Then ssh was run as "ssh -o BatchMode=yes root@server.example.eu", over the walk's connection, and the line "python3 -I - --name 'example.eu team sites'"
    And the value came on ssh's standard input, and in no argv

  Scenario: The admin channel keeps one connection, in a directory only this user may enter, and closes it
    Given the walk is given --server "server.example.eu"
    When I make the channels for the namespace "example"
    And the server runs "true" with a script on its standard input
    And the server runs "true" with a script on its standard input
    Then both commands named the same ControlPath, in a directory of mode 0700
    When the walk's connection is closed
    Then ssh was asked to close it, and its directory is gone

  Scenario: The deploy channel sends each call as a JSON line in the unit's namespace, over one connection
    Given the walk is given --kubectl "ssh -i key deploy@server.example.eu {unit} kubectl-read"
    When I make the channels for the namespace "example"
    And kubectl gets the CronJob "worker" twice
    Then ssh was started once, as "ssh -i key deploy@server.example.eu example kubectl-read"
    And the verb was sent ["get", "cronjob", "worker", "-o", "json"] twice
    And kubectl's answer was the verb's

  Scenario: The deploy channel takes no input
    Given the walk is given --kubectl "ssh -i key deploy@server.example.eu {unit} kubectl-read"
    When I make the channels for the namespace "example"
    And kubectl applies a Secret manifest holding a value
    Then kubectl's answer is exit 2, "kubectl-read reads only: it takes no input"
    And ssh was never run

  Scenario: Over the deploy channel a Secret's keys come from the server's secret-keys verb
    Given the example service
    And the walk reads the cluster over the deploy channel
    When I run pubkit "deploy" "walk"
    Then "k8s.secret:example-files" is ready, "Secret example-files in example has keys token; their values are not checked over the deploy channel"
    And "k8s.namespace:example" is unknown, "judged over the admin channel only"
    And no output holds a value

  Scenario: Over the deploy channel a Secret without a key the manifests read is drift
    Given the example service
    And the walk reads the cluster over the deploy channel
    And the Secret "example-files" has no key "token"
    When I run pubkit "deploy" "walk"
    Then "k8s.secret:example-files" is drift, "Secret example-files has no key token"

  Scenario: Where the server has no secret-keys verb yet, a Secret is looked for by name alone
    Given the example service
    And the walk reads the cluster over the deploy channel
    And the server has no secret-keys verb
    When I run pubkit "deploy" "walk"
    Then "k8s.secret:example-files" is ready, "Secret example-files is in example"
    And it prints "keys not checked on this channel: the server has no secret-keys verb"
    And kubectl asked for no Secret's data
    And no output holds a value
