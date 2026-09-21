## Description

<!-- Describe your changes in detail -->

## Type of Change

<!-- Mark relevant options with an x -->

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update
- [ ] Performance improvement
- [ ] Code refactoring
- [ ] Test addition or improvement

## Related Surfaces

<!-- Which harness surfaces are affected? Mark all that apply -->

- [ ] identity
- [ ] instructions
- [ ] tools
- [ ] skills
- [ ] mcps
- [ ] memory
- [ ] sandbox
- [ ] model_defaults
- [ ] routing
- [ ] orchestration
- [ ] data_gateway
- [ ] evaluator
- [ ] telemetry
- [ ] artifacts
- [ ] secrets_policy
- [ ] policy_engine
- [ ] knowledge_graph
- [ ] swarm

## Testing

<!-- Describe the tests you ran -->

- [ ] All existing tests pass (`make test`)
- [ ] New tests added for new functionality
- [ ] Integration tests pass (`make test` with integration markers)
- [ ] Manual testing performed

## Checklist

- [ ] My code follows the project's style guidelines (`make lint` passes)
- [ ] I have performed a self-review of my own code
- [ ] I have commented my code, particularly in hard-to-understand areas
- [ ] I have made corresponding changes to the documentation
- [ ] My changes generate no new warnings
- [ ] I have added tests that prove my fix is effective or that my feature works
- [ ] New and existing unit tests pass locally with my changes

## Governance

<!-- For harness patch proposals only -->

- [ ] This change has been evaluated by the AcceptanceSuite
- [ ] PolicyEngine.validate_patch() passes
- [ ] The change includes appropriate inverse patches for rollback
- [ ] Trace records will be generated for this change

## Additional Notes

<!-- Any additional context, screenshots, or notes -->
