import asyncio
import unittest

from app.routers.pages import privacy_notice, terms_of_service


class PublicPagesTest(unittest.TestCase):
    def test_privacy_notice_describes_the_deployed_service(self):
        page = asyncio.run(privacy_notice())

        self.assertIn("finance.passi.cloud", page)
        self.assertIn("Passi", page)
        self.assertIn("Enable Banking", page)
        self.assertIn("PostgreSQL", page)
        self.assertIn("admin@passi.cloud", page)
        self.assertIn("request deletion", page)
        self.assertNotIn("only on the owner's computer", page)

    def test_terms_describe_the_deployed_service(self):
        page = asyncio.run(terms_of_service())

        self.assertIn("finance.passi.cloud", page)
        self.assertIn("Passi", page)
        self.assertIn("Enable Banking", page)
        self.assertIn("admin@passi.cloud", page)
        self.assertIn("authorised users", page)
        self.assertNotIn("local-only", page)
if __name__ == "__main__":
    unittest.main()
