"""Steps for the {name} command."""

from pytest_bdd import parsers, scenarios, when

scenarios("../features/cli.feature")


@when(parsers.parse('I run {name} "{arg}"'), target_fixture="result")
def run_program(run, program, arg):
    return run(program, arg)


@when("I run {name} with no arguments", target_fixture="result")
def run_bare(run, program):
    return run(program)
