"""Onboarding and receipt contracts, without Telegram sends or panel mutations."""
import unittest
from datetime import datetime, timezone, timedelta
from handlers.instructions import guide_screen, DEVICES, PLATFORMS, APPS
from handlers.access_key import key_text
from handlers.subscription_service import receipt_text

class GuideTests(unittest.TestCase):
    def test_every_device_has_three_short_steps_and_visible_help(self):
        for device in DEVICES:
            for step in range(3):
                text,keyboard=guide_screen(device,step)
                self.assertLess(len(text),700)
                self.assertIn(f'{step+1} из 3',text)
                callbacks=[button.callback_data for row in keyboard.inline_keyboard for button in row]
                self.assertIn('guide_support',callbacks)
                self.assertNotIn('запасн',text.lower())
    def test_alternative_apps_keep_the_selected_client_between_steps(self):
        for device,platform in PLATFORMS.items():
            self.assertIn('happ',platform['apps'])
            for app in platform['apps']:
                for step in range(3):
                    text,keyboard=guide_screen(device,step,app)
                    self.assertLess(len(text),700)
                    self.assertIn(APPS[app]['name'],text)
                    callbacks=[b.callback_data for row in keyboard.inline_keyboard for b in row if b.callback_data]
                    if step<2:self.assertIn(f'guide_{device}_{step+1}_{app}',callbacks)
                    self.assertTrue(all(len(c.encode())<=64 for c in callbacks))
                    self.assertIn('guide_support',callbacks)
    def test_unavailable_region_explanation_and_real_downloads(self):
        from urllib.parse import urlparse
        allowed={'apps.apple.com','play.google.com','github.com','www.happ.su','v2raytun.com'}
        for device,platform in PLATFORMS.items():
            text,_=guide_screen(device,0)
            if device in ('ios','android','mac'):self.assertIn('регионе',text)
            for app in platform['apps']:
                url=urlparse(APPS[app]['downloads'][device])
                self.assertEqual(url.scheme,'https');self.assertIn(url.netloc,allowed)
    def test_key_single_link_and_html_escaped(self):
        text=key_text({'sub_id':'test-guide-fixture','subscription_expires_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()})
        self.assertEqual(text.count('<pre>'),1)
        self.assertNotIn('XUI',text)
        self.assertNotIn('запасн',text.lower())
    def test_blocked_account_does_not_disclose_key(self):
        self.assertNotIn('<pre>',key_text({'sub_id':'test-guide-fixture','is_blocked':True}))
    def test_renewal_receipt_single_link(self):
        text=receipt_text('premium',True,datetime.now(timezone.utc)+timedelta(days=30),'test-guide-fixture',0)
        self.assertEqual(text.count('<pre>'),1)
        self.assertIn('Ссылку менять не нужно',text)
        self.assertNotIn('запасн',text.lower())

if __name__=='__main__':unittest.main()
