"""The reversible UI switch exposes no new write surface."""
import os
import unittest
from unittest.mock import patch

import notta_service


class SimpleUiConfigurationTest(unittest.TestCase):
    def test_new_ui_requires_workspace_planning_and_explicit_switch(self):
        for workspace, planning, simple, expected in (
            ('yes', 'yes', 'yes', True), ('yes', 'yes', 'no', False),
            ('yes', 'no', 'yes', False), ('no', 'yes', 'yes', False),
            ('yes', 'yes', '', False), ('yes', 'yes', 'true', False),
        ):
            with self.subTest(workspace=workspace, planning=planning, simple=simple):
                with patch.dict(os.environ, {'NOTTA_WORKSPACE_ENABLED': workspace,
                                              'NOTTA_PLANNING_ENABLED': planning,
                                              'NOTTA_SIMPLE_UI_ENABLED': simple}):
                    self.assertIs(notta_service.configuration()['simpleUiMode'], expected)

    def test_turning_off_new_ui_keeps_planning_and_free_entitlement_available(self):
        with patch.dict(os.environ, {'NOTTA_WORKSPACE_ENABLED': 'yes', 'NOTTA_PLANNING_ENABLED': 'yes',
                                     'NOTTA_SIMPLE_UI_ENABLED': 'no'}):
            config = notta_service.configuration()
        self.assertTrue(config['workspaceMode'])
        self.assertTrue(config['planningMode'])
        self.assertFalse(config['simpleUiMode'])
        self.assertEqual(config['initialProjectPrice'], 0)
        self.assertEqual(config['workspaceAiLimit'], 30)
