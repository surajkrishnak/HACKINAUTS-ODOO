from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

User = get_user_model()


class RolePermissionMatrixTests(TestCase):
    """
    Automated security verification testing the full (URL x Role) permission matrix.
    Ensures that every view enforces authentication and strict role barriers.
    """

    def setUp(self):
        self.client = Client()

        self.admin = User.objects.create_superuser(
            email="admin@matrix.test",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
        )
        self.staff = User.objects.create_user(
            email="staff@matrix.test",
            password="StaffPassword123!",
            role=User.Role.STAFF,
        )
        self.customer = User.objects.create_user(
            email="customer@matrix.test",
            password="CustomerPass123!",
            role=User.Role.CUSTOMER,
        )

    def test_permission_matrix(self):
        """
        Matrix test verifying HTTP response codes across all authenticated roles and anonymous users.
        """
        # (url_path, expected_admin, expected_staff, expected_customer, expected_anon)
        matrix = [
            ("/accounts/staff/", 200, 403, 403, 302),
            ("/catalog/products/", 200, 200, 403, 302),
            ("/catalog/discounts/", 200, 403, 403, 302),
            ("/subscriptions/templates/", 200, 200, 403, 302),
            ("/subscriptions/", 200, 200, 403, 302),
            ("/billing/invoices/", 200, 200, 403, 302),
            ("/billing/payments/", 200, 200, 403, 302),
            ("/reports/", 200, 200, 403, 302),
            ("/reports/export/?type=invoices", 200, 200, 403, 302),
            ("/portal/subscriptions/", 200, 403, 200, 302),
            ("/portal/invoices/", 200, 403, 200, 302),
            ("/portal/discount-request/", 200, 403, 200, 302),
            ("/portal/admin/discount-requests/", 200, 200, 403, 302),
        ]

        for url, exp_admin, exp_staff, exp_cust, exp_anon in matrix:
            with self.subTest(url=url):
                # 1. Anonymous test
                self.client.logout()
                resp_anon = self.client.get(url)
                self.assertEqual(
                    resp_anon.status_code,
                    exp_anon,
                    f"Anonymous user on {url}: expected {exp_anon}, got {resp_anon.status_code}",
                )

                # 2. Customer test
                self.client.force_login(self.customer)
                resp_cust = self.client.get(url)
                self.assertEqual(
                    resp_cust.status_code,
                    exp_cust,
                    f"Customer on {url}: expected {exp_cust}, got {resp_cust.status_code}",
                )

                # 3. Staff test
                self.client.force_login(self.staff)
                resp_staff = self.client.get(url)
                self.assertEqual(
                    resp_staff.status_code,
                    exp_staff,
                    f"Staff on {url}: expected {exp_staff}, got {resp_staff.status_code}",
                )

                # 4. Admin test
                self.client.force_login(self.admin)
                resp_admin = self.client.get(url)
                self.assertEqual(
                    resp_admin.status_code,
                    exp_admin,
                    f"Admin on {url}: expected {exp_admin}, got {resp_admin.status_code}",
                )

    def test_root_dispatcher_routing(self):
        """Verifies root URL '/' dispatches dynamically based on authenticated role."""
        # Anonymous visitor sees landing page
        self.client.logout()
        resp_anon = self.client.get("/")
        self.assertEqual(resp_anon.status_code, 200)
        self.assertContains(resp_anon, "Brightloop Software")

        # Customer is redirected to customer portal
        self.client.force_login(self.customer)
        resp_cust = self.client.get("/")
        self.assertEqual(resp_cust.status_code, 302)
        self.assertIn("/portal/subscriptions/", resp_cust.url)

        # Staff is served the dashboard
        self.client.force_login(self.staff)
        resp_staff = self.client.get("/")
        self.assertEqual(resp_staff.status_code, 200)
        self.assertContains(resp_staff, "Executive Finance Dashboard")

        # Admin is served the dashboard with profit analysis
        self.client.force_login(self.admin)
        resp_admin = self.client.get("/")
        self.assertEqual(resp_admin.status_code, 200)
        self.assertContains(resp_admin, "Profitability & Cost Analysis")
