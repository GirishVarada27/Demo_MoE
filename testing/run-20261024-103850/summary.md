# Certificate equivalency agent: functional test run

- **Overall:** PASS
- **Started:** 2026-10-24T10:38:50  **Finished:** 2026-10-24T10:40:29  (98.9s)
- **Totals:** 124 tests: 124 passed, 0 failed, 0 errors, 0 skipped
- **Filter:** none (all tests)

## By test file

| File | Passed | Failed | Errors | Skipped |
|---|---|---|---|---|
| `tests/test_acceptance.py` | 11 | 0 | 0 | 0 |
| `tests/test_agent.py` | 17 | 0 | 0 | 0 |
| `tests/test_app001_and_labeling_fixes.py` | 4 | 0 | 0 | 0 |
| `tests/test_app003_tool_budget.py` | 5 | 0 | 0 | 0 |
| `tests/test_app010_assumption_rules.py` | 1 | 0 | 0 | 0 |
| `tests/test_change_of_circumstances.py` | 8 | 0 | 0 | 0 |
| `tests/test_config.py` | 4 | 0 | 0 | 0 |
| `tests/test_escalation_bugfix.py` | 5 | 0 | 0 | 0 |
| `tests/test_informational_and_escalation_state.py` | 5 | 0 | 0 | 0 |
| `tests/test_no_rule_found_escalation.py` | 2 | 0 | 0 | 0 |
| `tests/test_scenarios.py` | 6 | 0 | 0 | 0 |
| `tests/test_tools.py` | 26 | 0 | 0 | 0 |
| `tests/test_user_fact_overrides.py` | 5 | 0 | 0 | 0 |
| `tests/test_validation.py` | 25 | 0 | 0 | 0 |

## Failures

None.

## All tests

| # | Status | Time | Test |
|---|---|---|---|
| 1 | PASSED | 0.82s | [`tests/test_acceptance.py::test_acceptance_scenario[app001_complete_file_provisional_pass]`](results/001_test_acceptance.py__test_acceptance_scenario_app001_complete_file_provisional_pass.txt) |
| 2 | PASSED | 0.75s | [`tests/test_acceptance.py::test_acceptance_scenario[change_of_circumstances_transcript_unavailable]`](results/002_test_acceptance.py__test_acceptance_scenario_change_of_circumstances_transcript_unavailable.txt) |
| 3 | PASSED | 0.82s | [`tests/test_acceptance.py::test_acceptance_scenario[no_rule_found_app006]`](results/003_test_acceptance.py__test_acceptance_scenario_no_rule_found_app006.txt) |
| 4 | PASSED | 0.79s | [`tests/test_acceptance.py::test_acceptance_scenario[informational_documents_question]`](results/004_test_acceptance.py__test_acceptance_scenario_informational_documents_question.txt) |
| 5 | PASSED | 1.12s | [`tests/test_acceptance.py::test_acceptance_scenario[escalation_continuity_no_duplicate_ticket]`](results/005_test_acceptance.py__test_acceptance_scenario_escalation_continuity_no_duplicate_ticket.txt) |
| 6 | PASSED | 0.93s | [`tests/test_acceptance.py::test_acceptance_scenario[failure_injection_forces_retry_then_escalate]`](results/006_test_acceptance.py__test_acceptance_scenario_failure_injection_forces_retry_then_escalate.txt) |
| 7 | PASSED | 0.81s | [`tests/test_acceptance.py::test_acceptance_scenario[prompt_injection_fabricated_approval_is_blocked]`](results/007_test_acceptance.py__test_acceptance_scenario_prompt_injection_fabricated_approval_is_blocked.txt) |
| 8 | PASSED | 0.77s | [`tests/test_acceptance.py::test_acceptance_scenario[missing_applicant_id_asks_instead_of_guessing]`](results/008_test_acceptance.py__test_acceptance_scenario_missing_applicant_id_asks_instead_of_guessing.txt) |
| 9 | PASSED | 0.76s | [`tests/test_acceptance.py::test_acceptance_scenario[unsupported_out_of_domain_request]`](results/009_test_acceptance.py__test_acceptance_scenario_unsupported_out_of_domain_request.txt) |
| 10 | PASSED | 0.76s | [`tests/test_acceptance.py::test_acceptance_scenario[arabic_message_informational_question]`](results/010_test_acceptance.py__test_acceptance_scenario_arabic_message_informational_question.txt) |
| 11 | PASSED | 1.04s | [`tests/test_acceptance.py::test_acceptance_matrix_table`](results/011_test_acceptance.py__test_acceptance_matrix_table.txt) |
| 12 | PASSED | 0.77s | [`tests/test_agent.py::test_run_turn_no_tools_needed`](results/012_test_agent.py__test_run_turn_no_tools_needed.txt) |
| 13 | PASSED | 0.77s | [`tests/test_agent.py::test_run_turn_calls_tool_then_answers`](results/013_test_agent.py__test_run_turn_calls_tool_then_answers.txt) |
| 14 | PASSED | 0.76s | [`tests/test_agent.py::test_run_turn_updates_case_state_on_contradictory_curriculum_without_restarting`](results/014_test_agent.py__test_run_turn_updates_case_state_on_contradictory_curriculum_without_restarting.txt) |
| 15 | PASSED | 0.75s | [`tests/test_agent.py::test_run_turn_flags_contradictory_applicant_curriculum_as_open_question`](results/015_test_agent.py__test_run_turn_flags_contradictory_applicant_curriculum_as_open_question.txt) |
| 16 | PASSED | 0.76s | [`tests/test_agent.py::test_run_turn_injects_case_state_into_context`](results/016_test_agent.py__test_run_turn_injects_case_state_into_context.txt) |
| 17 | PASSED | 0.95s | [`tests/test_agent.py::test_run_turn_tool_call_limit_triggers_escalation`](results/017_test_agent.py__test_run_turn_tool_call_limit_triggers_escalation.txt) |
| 18 | PASSED | 0.79s | [`tests/test_agent.py::test_run_turn_tool_call_limit_reports_last_failed_claim`](results/018_test_agent.py__test_run_turn_tool_call_limit_reports_last_failed_claim.txt) |
| 19 | PASSED | 0.77s | [`tests/test_agent.py::test_run_turn_rewrites_twice_then_escalates_on_persistent_validation_failure`](results/019_test_agent.py__test_run_turn_rewrites_twice_then_escalates_on_persistent_validation_failure.txt) |
| 20 | PASSED | 0.76s | [`tests/test_agent.py::test_run_turn_revision_succeeds_on_second_draft`](results/020_test_agent.py__test_run_turn_revision_succeeds_on_second_draft.txt) |
| 21 | PASSED | 0.78s | [`tests/test_agent.py::test_run_turn_llm_verifier_fail_triggers_revision_then_escalation`](results/021_test_agent.py__test_run_turn_llm_verifier_fail_triggers_revision_then_escalation.txt) |
| 22 | PASSED | 0.81s | [`tests/test_agent.py::test_run_turn_case_escalation_shows_exact_failing_claims`](results/022_test_agent.py__test_run_turn_case_escalation_shows_exact_failing_claims.txt) |
| 23 | PASSED | 0.79s | [`tests/test_agent.py::test_run_turn_eligibility_statement_without_evaluate_eligibility_fails_validation`](results/023_test_agent.py__test_run_turn_eligibility_statement_without_evaluate_eligibility_fails_validation.txt) |
| 24 | PASSED | 0.76s | [`tests/test_agent.py::test_run_turn_tool_failure_retries_once_then_escalates`](results/024_test_agent.py__test_run_turn_tool_failure_retries_once_then_escalates.txt) |
| 25 | PASSED | 0.78s | [`tests/test_agent.py::test_run_turn_inject_failures_forces_simulate_failure`](results/025_test_agent.py__test_run_turn_inject_failures_forces_simulate_failure.txt) |
| 26 | PASSED | 0.77s | [`tests/test_agent.py::test_run_turn_tool_failure_recovers_on_retry`](results/026_test_agent.py__test_run_turn_tool_failure_recovers_on_retry.txt) |
| 27 | PASSED | 1.88s | [`tests/test_agent.py::test_build_client_uses_azure_openai_when_endpoint_set`](results/027_test_agent.py__test_build_client_uses_azure_openai_when_endpoint_set.txt) |
| 28 | PASSED | 1.76s | [`tests/test_agent.py::test_build_client_uses_plain_openai_when_endpoint_unset`](results/028_test_agent.py__test_build_client_uses_plain_openai_when_endpoint_unset.txt) |
| 29 | PASSED | 0.76s | [`tests/test_app001_and_labeling_fixes.py::test_app001_complete_file_gets_provisional_result_without_escalation`](results/029_test_app001_and_labeling_fixes.py__test_app001_complete_file_gets_provisional_result_without_escalation.txt) |
| 30 | PASSED | 0.79s | [`tests/test_app001_and_labeling_fixes.py::test_at_most_one_escalation_ticket_per_case_is_reused`](results/030_test_app001_and_labeling_fixes.py__test_at_most_one_escalation_ticket_per_case_is_reused.txt) |
| 31 | PASSED | 0.75s | [`tests/test_app001_and_labeling_fixes.py::test_documents_question_still_calls_only_search_requirements`](results/031_test_app001_and_labeling_fixes.py__test_documents_question_still_calls_only_search_requirements.txt) |
| 32 | PASSED | 0.75s | [`tests/test_app001_and_labeling_fixes.py::test_check_documents_summary_never_claims_passport_or_emirates_id_are_attested`](results/032_test_app001_and_labeling_fixes.py__test_check_documents_summary_never_claims_passport_or_emirates_id_are_attested.txt) |
| 33 | PASSED | 0.78s | [`tests/test_app003_tool_budget.py::test_app003_asks_for_emirates_id_with_zero_escalations`](results/033_test_app003_tool_budget.py__test_app003_asks_for_emirates_id_with_zero_escalations.txt) |
| 34 | PASSED | 0.78s | [`tests/test_app003_tool_budget.py::test_missing_document_draft_that_doesnt_ask_for_it_fails_validation`](results/034_test_app003_tool_budget.py__test_missing_document_draft_that_doesnt_ask_for_it_fails_validation.txt) |
| 35 | PASSED | 0.72s | [`tests/test_app003_tool_budget.py::test_greeting_causes_no_record_user_fact_call`](results/035_test_app003_tool_budget.py__test_greeting_causes_no_record_user_fact_call.txt) |
| 36 | PASSED | 0.74s | [`tests/test_app003_tool_budget.py::test_bare_applicant_id_never_records_a_user_fact`](results/036_test_app003_tool_budget.py__test_bare_applicant_id_never_records_a_user_fact.txt) |
| 37 | PASSED | 0.74s | [`tests/test_app003_tool_budget.py::test_validate_draft_before_evaluate_eligibility_returns_ordering_error`](results/037_test_app003_tool_budget.py__test_validate_draft_before_evaluate_eligibility_returns_ordering_error.txt) |
| 38 | PASSED | 0.76s | [`tests/test_app010_assumption_rules.py::test_app010_answers_with_cbse_threshold_under_assumed_and_no_escalation`](results/038_test_app010_assumption_rules.py__test_app010_answers_with_cbse_threshold_under_assumed_and_no_escalation.txt) |
| 39 | PASSED | 0.79s | [`tests/test_change_of_circumstances.py::test_transcript_attestation_blocker_is_recorded_and_changes_the_plan`](results/039_test_change_of_circumstances.py__test_transcript_attestation_blocker_is_recorded_and_changes_the_plan.txt) |
| 40 | PASSED | 0.78s | [`tests/test_change_of_circumstances.py::test_record_user_fact_conflicting_with_tool_reported_fact_is_flagged`](results/040_test_change_of_circumstances.py__test_record_user_fact_conflicting_with_tool_reported_fact_is_flagged.txt) |
| 41 | PASSED | 0.74s | [`tests/test_change_of_circumstances.py::test_duplicate_identical_tool_call_in_one_turn_is_not_reinvoked`](results/041_test_change_of_circumstances.py__test_duplicate_identical_tool_call_in_one_turn_is_not_reinvoked.txt) |
| 42 | PASSED | 0.76s | [`tests/test_change_of_circumstances.py::test_bare_greeting_runs_no_checks`](results/042_test_change_of_circumstances.py__test_bare_greeting_runs_no_checks.txt) |
| 43 | PASSED | 0.75s | [`tests/test_change_of_circumstances.py::test_prompt_instructs_no_checks_on_bare_greeting_and_recording_user_facts`](results/043_test_change_of_circumstances.py__test_prompt_instructs_no_checks_on_bare_greeting_and_recording_user_facts.txt) |
| 44 | PASSED | 0.74s | [`tests/test_change_of_circumstances.py::test_draft_application_without_determinable_eligibility_requires_caveat`](results/044_test_change_of_circumstances.py__test_draft_application_without_determinable_eligibility_requires_caveat.txt) |
| 45 | PASSED | 0.74s | [`tests/test_change_of_circumstances.py::test_draft_application_with_determinable_eligibility_needs_no_caveat`](results/045_test_change_of_circumstances.py__test_draft_application_with_determinable_eligibility_needs_no_caveat.txt) |
| 46 | PASSED | 0.75s | [`tests/test_change_of_circumstances.py::test_draft_application_without_any_eligibility_check_requires_caveat`](results/046_test_change_of_circumstances.py__test_draft_application_without_any_eligibility_check_requires_caveat.txt) |
| 47 | PASSED | 0.71s | [`tests/test_config.py::test_azure_mode_selected_when_endpoint_set`](results/047_test_config.py__test_azure_mode_selected_when_endpoint_set.txt) |
| 48 | PASSED | 0.7s | [`tests/test_config.py::test_azure_mode_missing_vars_raises_with_names`](results/048_test_config.py__test_azure_mode_missing_vars_raises_with_names.txt) |
| 49 | PASSED | 0.71s | [`tests/test_config.py::test_openai_mode_selected_when_endpoint_unset`](results/049_test_config.py__test_openai_mode_selected_when_endpoint_unset.txt) |
| 50 | PASSED | 0.71s | [`tests/test_config.py::test_openai_mode_missing_vars_raises_with_names`](results/050_test_config.py__test_openai_mode_missing_vars_raises_with_names.txt) |
| 51 | PASSED | 0.8s | [`tests/test_escalation_bugfix.py::test_trace_shows_exact_validator_failure_reason_and_draft`](results/051_test_escalation_bugfix.py__test_trace_shows_exact_validator_failure_reason_and_draft.txt) |
| 52 | PASSED | 0.77s | [`tests/test_escalation_bugfix.py::test_evaluate_eligibility_app009_verdicts_are_correct_not_buggy`](results/052_test_escalation_bugfix.py__test_evaluate_eligibility_app009_verdicts_are_correct_not_buggy.txt) |
| 53 | PASSED | 0.75s | [`tests/test_escalation_bugfix.py::test_app009_complete_file_with_undetermined_threshold_does_not_force_escalation`](results/053_test_escalation_bugfix.py__test_app009_complete_file_with_undetermined_threshold_does_not_force_escalation.txt) |
| 54 | PASSED | 0.74s | [`tests/test_escalation_bugfix.py::test_prompt_forces_no_tool_sequence_and_guides_generic_questions`](results/054_test_escalation_bugfix.py__test_prompt_forces_no_tool_sequence_and_guides_generic_questions.txt) |
| 55 | PASSED | 0.75s | [`tests/test_escalation_bugfix.py::test_documents_question_naming_an_applicant_still_calls_search_only`](results/055_test_escalation_bugfix.py__test_documents_question_naming_an_applicant_still_calls_search_only.txt) |
| 56 | PASSED | 0.77s | [`tests/test_informational_and_escalation_state.py::test_documents_question_never_creates_a_ticket_even_when_validation_keeps_failing`](results/056_test_informational_and_escalation_state.py__test_documents_question_never_creates_a_ticket_even_when_validation_keeps_fa.txt) |
| 57 | PASSED | 0.78s | [`tests/test_informational_and_escalation_state.py::test_documents_question_model_calling_escalate_to_human_directly_is_declined`](results/057_test_informational_and_escalation_state.py__test_documents_question_model_calling_escalate_to_human_directly_is_declined.txt) |
| 58 | PASSED | 0.79s | [`tests/test_informational_and_escalation_state.py::test_second_turn_after_escalation_references_existing_ticket_not_a_new_one`](results/058_test_informational_and_escalation_state.py__test_second_turn_after_escalation_references_existing_ticket_not_a_new_one.txt) |
| 59 | PASSED | 0.76s | [`tests/test_informational_and_escalation_state.py::test_existing_escalation_referenced_rejects_a_proposal_to_escalate_again`](results/059_test_informational_and_escalation_state.py__test_existing_escalation_referenced_rejects_a_proposal_to_escalate_again.txt) |
| 60 | PASSED | 0.8s | [`tests/test_informational_and_escalation_state.py::test_model_escalating_again_is_redirected_to_append_not_a_new_ticket`](results/060_test_informational_and_escalation_state.py__test_model_escalating_again_is_redirected_to_append_not_a_new_ticket.txt) |
| 61 | PASSED | 0.76s | [`tests/test_no_rule_found_escalation.py::test_app006_greeting_escalates_on_missing_rule_not_validation_failure`](results/061_test_no_rule_found_escalation.py__test_app006_greeting_escalates_on_missing_rule_not_validation_failure.txt) |
| 62 | PASSED | 0.77s | [`tests/test_no_rule_found_escalation.py::test_handle_no_rule_found_defers_when_search_actually_finds_a_match`](results/062_test_no_rule_found_escalation.py__test_handle_no_rule_found_defers_when_search_actually_finds_a_match.txt) |
| 63 | PASSED | 0.75s | [`tests/test_scenarios.py::test_documents_question_uses_search_requirements_only`](results/063_test_scenarios.py__test_documents_question_uses_search_requirements_only.txt) |
| 64 | PASSED | 0.75s | [`tests/test_scenarios.py::test_missing_anything_question_looks_up_applicant_then_checks_documents`](results/064_test_scenarios.py__test_missing_anything_question_looks_up_applicant_then_checks_documents.txt) |
| 65 | PASSED | 0.79s | [`tests/test_scenarios.py::test_cannot_get_attestation_triggers_revalidation_and_changed_plan`](results/065_test_scenarios.py__test_cannot_get_attestation_triggers_revalidation_and_changed_plan.txt) |
| 66 | PASSED | 0.75s | [`tests/test_scenarios.py::test_contradictory_curriculum_is_detected_and_flagged_as_open_question`](results/066_test_scenarios.py__test_contradictory_curriculum_is_detected_and_flagged_as_open_question.txt) |
| 67 | PASSED | 0.77s | [`tests/test_scenarios.py::test_tool_failure_is_handled_gracefully_and_escalated`](results/067_test_scenarios.py__test_tool_failure_is_handled_gracefully_and_escalated.txt) |
| 68 | PASSED | 0.8s | [`tests/test_scenarios.py::test_unsupported_request_is_declined_with_a_next_step`](results/068_test_scenarios.py__test_unsupported_request_is_declined_with_a_next_step.txt) |
| 69 | PASSED | 0.75s | [`tests/test_tools.py::test_tool_registry_is_consistent`](results/069_test_tools.py__test_tool_registry_is_consistent.txt) |
| 70 | PASSED | 0.74s | [`tests/test_tools.py::test_load_rule_chunks_has_official_and_assumption_labels`](results/070_test_tools.py__test_load_rule_chunks_has_official_and_assumption_labels.txt) |
| 71 | PASSED | 0.74s | [`tests/test_tools.py::test_search_requirements_simulate_failure`](results/071_test_tools.py__test_search_requirements_simulate_failure.txt) |
| 72 | PASSED | 0.74s | [`tests/test_tools.py::test_search_requirements_ranks_relevant_chunk_first`](results/072_test_tools.py__test_search_requirements_ranks_relevant_chunk_first.txt) |
| 73 | PASSED | 0.76s | [`tests/test_tools.py::test_search_requirements_returns_empty_when_nothing_relevant`](results/073_test_tools.py__test_search_requirements_returns_empty_when_nothing_relevant.txt) |
| 74 | PASSED | 0.75s | [`tests/test_tools.py::test_get_applicant_record_found`](results/074_test_tools.py__test_get_applicant_record_found.txt) |
| 75 | PASSED | 0.75s | [`tests/test_tools.py::test_get_applicant_record_not_found`](results/075_test_tools.py__test_get_applicant_record_not_found.txt) |
| 76 | PASSED | 0.73s | [`tests/test_tools.py::test_get_applicant_record_simulate_failure`](results/076_test_tools.py__test_get_applicant_record_simulate_failure.txt) |
| 77 | PASSED | 0.74s | [`tests/test_tools.py::test_check_documents_complete_file`](results/077_test_tools.py__test_check_documents_complete_file.txt) |
| 78 | PASSED | 0.74s | [`tests/test_tools.py::test_check_documents_missing_attested_transcript`](results/078_test_tools.py__test_check_documents_missing_attested_transcript.txt) |
| 79 | PASSED | 0.74s | [`tests/test_tools.py::test_check_documents_missing_identity_document`](results/079_test_tools.py__test_check_documents_missing_identity_document.txt) |
| 80 | PASSED | 0.75s | [`tests/test_tools.py::test_check_documents_contradictory_curriculum`](results/080_test_tools.py__test_check_documents_contradictory_curriculum.txt) |
| 81 | PASSED | 0.75s | [`tests/test_tools.py::test_check_documents_expired_document`](results/081_test_tools.py__test_check_documents_expired_document.txt) |
| 82 | PASSED | 0.74s | [`tests/test_tools.py::test_check_documents_unknown_applicant`](results/082_test_tools.py__test_check_documents_unknown_applicant.txt) |
| 83 | PASSED | 0.75s | [`tests/test_tools.py::test_check_documents_simulate_failure`](results/083_test_tools.py__test_check_documents_simulate_failure.txt) |
| 84 | PASSED | 0.75s | [`tests/test_tools.py::test_evaluate_eligibility_american_exception_case_passes`](results/084_test_tools.py__test_evaluate_eligibility_american_exception_case_passes.txt) |
| 85 | PASSED | 0.75s | [`tests/test_tools.py::test_evaluate_eligibility_american_without_msat_leaves_exception_path_undeterminable`](results/085_test_tools.py__test_evaluate_eligibility_american_without_msat_leaves_exception_path_undeterminable.txt) |
| 86 | PASSED | 0.77s | [`tests/test_tools.py::test_evaluate_eligibility_unsupported_curriculum_returns_none_chunk`](results/086_test_tools.py__test_evaluate_eligibility_unsupported_curriculum_returns_none_chunk.txt) |
| 87 | PASSED | 0.74s | [`tests/test_tools.py::test_evaluate_eligibility_indian_curriculum_flags_assumption`](results/087_test_tools.py__test_evaluate_eligibility_indian_curriculum_flags_assumption.txt) |
| 88 | PASSED | 0.74s | [`tests/test_tools.py::test_evaluate_eligibility_unknown_applicant`](results/088_test_tools.py__test_evaluate_eligibility_unknown_applicant.txt) |
| 89 | PASSED | 0.73s | [`tests/test_tools.py::test_evaluate_eligibility_simulate_failure`](results/089_test_tools.py__test_evaluate_eligibility_simulate_failure.txt) |
| 90 | PASSED | 0.76s | [`tests/test_tools.py::test_create_draft_application_writes_to_store`](results/090_test_tools.py__test_create_draft_application_writes_to_store.txt) |
| 91 | PASSED | 0.75s | [`tests/test_tools.py::test_create_draft_application_unknown_applicant`](results/091_test_tools.py__test_create_draft_application_unknown_applicant.txt) |
| 92 | PASSED | 0.78s | [`tests/test_tools.py::test_create_draft_application_simulate_failure`](results/092_test_tools.py__test_create_draft_application_simulate_failure.txt) |
| 93 | PASSED | 0.78s | [`tests/test_tools.py::test_escalate_to_human_writes_ticket`](results/093_test_tools.py__test_escalate_to_human_writes_ticket.txt) |
| 94 | PASSED | 0.76s | [`tests/test_tools.py::test_escalate_to_human_simulate_failure`](results/094_test_tools.py__test_escalate_to_human_simulate_failure.txt) |
| 95 | PASSED | 0.75s | [`tests/test_user_fact_overrides.py::test_document_override_flags_unavailable_attested_doc`](results/095_test_user_fact_overrides.py__test_document_override_flags_unavailable_attested_doc.txt) |
| 96 | PASSED | 0.71s | [`tests/test_user_fact_overrides.py::test_document_override_is_a_noop_without_a_matching_fact`](results/096_test_user_fact_overrides.py__test_document_override_is_a_noop_without_a_matching_fact.txt) |
| 97 | PASSED | 0.85s | [`tests/test_user_fact_overrides.py::test_eligibility_override_downgrades_pass_to_undeterminable`](results/097_test_user_fact_overrides.py__test_eligibility_override_downgrades_pass_to_undeterminable.txt) |
| 98 | PASSED | 0.74s | [`tests/test_user_fact_overrides.py::test_eligibility_override_is_a_noop_without_a_matching_fact`](results/098_test_user_fact_overrides.py__test_eligibility_override_is_a_noop_without_a_matching_fact.txt) |
| 99 | PASSED | 0.78s | [`tests/test_user_fact_overrides.py::test_transcript_unavailable_after_provisional_result_changes_plan_without_new_ticket`](results/099_test_user_fact_overrides.py__test_transcript_unavailable_after_provisional_result_changes_plan_without_new_ticket.txt) |
| 100 | PASSED | 0.73s | [`tests/test_validation.py::test_check_required_sections_all_present`](results/100_test_validation.py__test_check_required_sections_all_present.txt) |
| 101 | PASSED | 0.76s | [`tests/test_validation.py::test_check_required_sections_missing_one`](results/101_test_validation.py__test_check_required_sections_missing_one.txt) |
| 102 | PASSED | 0.73s | [`tests/test_validation.py::test_check_required_sections_missing_all`](results/102_test_validation.py__test_check_required_sections_missing_all.txt) |
| 103 | PASSED | 0.71s | [`tests/test_validation.py::test_requirement_claims_grounded_skips_trivial_verified_section`](results/103_test_validation.py__test_requirement_claims_grounded_skips_trivial_verified_section.txt) |
| 104 | PASSED | 0.72s | [`tests/test_validation.py::test_requirement_claims_grounded_fails_without_any_tool_history`](results/104_test_validation.py__test_requirement_claims_grounded_fails_without_any_tool_history.txt) |
| 105 | PASSED | 0.75s | [`tests/test_validation.py::test_requirement_claims_grounded_passes_with_official_search_result`](results/105_test_validation.py__test_requirement_claims_grounded_passes_with_official_search_result.txt) |
| 106 | PASSED | 0.71s | [`tests/test_validation.py::test_requirement_claims_grounded_ignores_assumption_only_search_result`](results/106_test_validation.py__test_requirement_claims_grounded_ignores_assumption_only_search_result.txt) |
| 107 | PASSED | 0.81s | [`tests/test_validation.py::test_requirement_claims_grounded_rejects_check_documents_alone`](results/107_test_validation.py__test_requirement_claims_grounded_rejects_check_documents_alone.txt) |
| 108 | PASSED | 0.73s | [`tests/test_validation.py::test_requirement_claims_grounded_rejects_verified_statement_citing_a_tool_result`](results/108_test_validation.py__test_requirement_claims_grounded_rejects_verified_statement_citing_a_tool_result.txt) |
| 109 | PASSED | 0.81s | [`tests/test_validation.py::test_requirement_claims_grounded_ignores_failed_check_documents`](results/109_test_validation.py__test_requirement_claims_grounded_ignores_failed_check_documents.txt) |
| 110 | PASSED | 0.66s | [`tests/test_validation.py::test_eligibility_statement_fails_without_evaluate_eligibility`](results/110_test_validation.py__test_eligibility_statement_fails_without_evaluate_eligibility.txt) |
| 111 | PASSED | 0.78s | [`tests/test_validation.py::test_eligibility_statement_passes_with_successful_evaluate_eligibility`](results/111_test_validation.py__test_eligibility_statement_passes_with_successful_evaluate_eligibility.txt) |
| 112 | PASSED | 0.8s | [`tests/test_validation.py::test_eligibility_statement_fails_with_failed_evaluate_eligibility`](results/112_test_validation.py__test_eligibility_statement_fails_with_failed_evaluate_eligibility.txt) |
| 113 | PASSED | 0.81s | [`tests/test_validation.py::test_no_eligibility_language_skips_check_entirely`](results/113_test_validation.py__test_no_eligibility_language_skips_check_entirely.txt) |
| 114 | PASSED | 1.06s | [`tests/test_validation.py::test_llm_verify_pass`](results/114_test_validation.py__test_llm_verify_pass.txt) |
| 115 | PASSED | 0.96s | [`tests/test_validation.py::test_llm_verify_fail_returns_reason`](results/115_test_validation.py__test_llm_verify_fail_returns_reason.txt) |
| 116 | PASSED | 0.83s | [`tests/test_validation.py::test_llm_verify_fail_with_no_reason_still_fails`](results/116_test_validation.py__test_llm_verify_fail_with_no_reason_still_fails.txt) |
| 117 | PASSED | 0.84s | [`tests/test_validation.py::test_validate_draft_passes_rule_based_and_llm_pass`](results/117_test_validation.py__test_validate_draft_passes_rule_based_and_llm_pass.txt) |
| 118 | PASSED | 0.83s | [`tests/test_validation.py::test_validate_draft_fails_on_missing_sections_without_calling_llm`](results/118_test_validation.py__test_validate_draft_fails_on_missing_sections_without_calling_llm.txt) |
| 119 | PASSED | 0.84s | [`tests/test_validation.py::test_validate_draft_fails_when_llm_verifier_flags_claim`](results/119_test_validation.py__test_validate_draft_fails_when_llm_verifier_flags_claim.txt) |
| 120 | PASSED | 0.78s | [`tests/test_validation.py::test_validate_draft_skips_llm_verifier_when_nothing_is_presented_as_verified`](results/120_test_validation.py__test_validate_draft_skips_llm_verifier_when_nothing_is_presented_as_verified.txt) |
| 121 | PASSED | 0.78s | [`tests/test_validation.py::test_assumed_and_simulated_claims_pass_without_official_chunk`](results/121_test_validation.py__test_assumed_and_simulated_claims_pass_without_official_chunk.txt) |
| 122 | PASSED | 0.72s | [`tests/test_validation.py::test_verified_claim_citing_only_an_assumption_chunk_fails_with_exact_claim`](results/122_test_validation.py__test_verified_claim_citing_only_an_assumption_chunk_fails_with_exact_claim.txt) |
| 123 | PASSED | 0.69s | [`tests/test_validation.py::test_only_the_ungrounded_verified_claim_fails`](results/123_test_validation.py__test_only_the_ungrounded_verified_claim_fails.txt) |
| 124 | PASSED | 0.73s | [`tests/test_validation.py::test_validate_draft_skips_llm_pass_when_create_not_provided`](results/124_test_validation.py__test_validate_draft_skips_llm_pass_when_create_not_provided.txt) |
