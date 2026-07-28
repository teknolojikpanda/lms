# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Tenant registry on the control site (Ek-4.3 /owner/tenants).

Tenant = Frappe site (ADR-0001), and creating a site means running
``bench new-site`` on the bench host with filesystem and database
privileges. **The web layer never does that.** These endpoints only record
intent and lifecycle state; an operator (or a CI job) runs the
provisioning CLI, which reads this registry and reports back. Keeping the
privileged step out of the request path means a compromised web session
cannot create or destroy sites.

Suspension is enforced here as registry state; the CLI applies it to the
tenant site itself.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.rate_limiter import rate_limit
from frappe.utils import now_datetime

from lms.lms.language_platform.tenant_rules import (
	TenantNameError,
	build_site_name,
	check_seat_capacity,
	seats_available,
	validate_subdomain,
)

# Status transitions the platform allows. Anything not listed is refused,
# so a tenant cannot jump from Requested straight to Active without the
# provisioning step actually having run.
ALLOWED_TRANSITIONS = {
	"Requested": {"Provisioning", "Archived"},
	"Provisioning": {"Active", "Requested", "Archived"},
	"Active": {"Suspended", "Archived"},
	"Suspended": {"Active", "Archived"},
	"Archived": set(),
}


class LMSTenant(Document):
	def validate(self):
		self.validate_subdomain_field()
		self.validate_seat_limit()

	def validate_subdomain_field(self):
		try:
			self.subdomain = validate_subdomain(self.subdomain)
		except TenantNameError as e:
			frappe.throw(str(e), title=_("Invalid Subdomain"))

		# The subdomain is the document name and the site directory; letting
		# it change after provisioning would orphan a live site.
		if not self.is_new():
			before = self.get_doc_before_save()
			if before and before.subdomain != self.subdomain and before.status != "Requested":
				frappe.throw(_("The subdomain cannot be changed once provisioning has started."))

		base_domain = frappe.conf.get("lms_platform_domain")
		if base_domain:
			try:
				self.site_name = build_site_name(self.subdomain, base_domain)
			except TenantNameError as e:
				frappe.throw(str(e), title=_("Invalid Site Name"))

	def validate_seat_limit(self):
		if self.seat_limit and self.seat_limit < 0:
			frappe.throw(_("Seat limit cannot be negative."))

	def transition_to(self, status: str, reason: str | None = None):
		"""Move to a new status, refusing transitions the lifecycle forbids."""
		if status not in ALLOWED_TRANSITIONS.get(self.status, set()):
			frappe.throw(
				_("Cannot move a tenant from {0} to {1}.").format(self.status, status),
				title=_("Invalid Transition"),
			)

		previous = self.status
		self.status = status
		if status == "Suspended":
			self.suspended_reason = reason
		elif status == "Active":
			self.suspended_reason = None
		self.save(ignore_permissions=True)
		self.add_comment(
			"Comment",
			_("Status changed {0} → {1}{2}").format(
				previous, status, f". Reason: {reason}" if reason else ""
			),
		)


def _require_owner():
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only platform owners can manage tenants."), frappe.PermissionError)


@frappe.whitelist()
@rate_limit(limit=60, seconds=60 * 60)
def register_tenant(
	tenant_name: str,
	subdomain: str,
	tenant_type: str = "Dershane",
	plan: str = "Pilot",
	seat_limit: int = 500,
	contact_person: str | None = None,
	contact_email: str | None = None,
) -> dict:
	"""Register a tenant. Provisioning is a separate, operator-run step."""
	_require_owner()

	doc = frappe.get_doc(
		{
			"doctype": "LMS Tenant",
			"tenant_name": tenant_name,
			"subdomain": subdomain,
			"tenant_type": tenant_type,
			"plan": plan,
			"seat_limit": seat_limit,
			"contact_person": contact_person,
			"contact_email": contact_email,
			"status": "Requested",
		}
	)
	doc.insert()
	doc.add_comment("Comment", _("Tenant registered by {0}.").format(frappe.session.user))

	return {
		"name": doc.name,
		"site_name": doc.site_name,
		"status": doc.status,
		"next_step": _(
			"Run the provisioning CLI on the bench host: "
			"python provisioning/provision_tenant.py --subdomain {0}"
		).format(doc.subdomain),
	}


@frappe.whitelist()
@rate_limit(limit=60, seconds=60 * 60)
def suspend_tenant(tenant: str, reason: str) -> dict:
	"""Suspend a tenant (Ek-4.3: suspend/activate).

	Registry state only — the CLI puts the site itself into maintenance
	mode. Both layers matter: this stops new provisioning actions, the
	site-level flag stops logins.
	"""
	_require_owner()
	if not (reason or "").strip():
		frappe.throw(_("A suspension reason is mandatory."))

	doc = frappe.get_doc("LMS Tenant", tenant)
	doc.transition_to("Suspended", reason.strip())
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
@rate_limit(limit=60, seconds=60 * 60)
def activate_tenant(tenant: str) -> dict:
	_require_owner()
	doc = frappe.get_doc("LMS Tenant", tenant)
	doc.transition_to("Active")
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def get_tenants() -> list[dict]:
	"""Tenant list for the owner portal, with seat headroom (Ek-4.3)."""
	_require_owner()

	tenants = frappe.get_all(
		"LMS Tenant",
		fields=[
			"name",
			"tenant_name",
			"subdomain",
			"site_name",
			"status",
			"tenant_type",
			"plan",
			"seat_limit",
			"active_students",
			"renewal_date",
			"provisioned_at",
		],
		order_by="tenant_name asc",
	)
	for tenant in tenants:
		available = seats_available(tenant.seat_limit, tenant.active_students)
		tenant["seats_available"] = None if available == -1 else available
	return tenants


@frappe.whitelist()
def check_tenant_capacity(tenant: str, adding: int) -> dict:
	"""Pre-flight check before a roster import (§1.3 onboarding)."""
	_require_owner()
	doc = frappe.get_doc("LMS Tenant", tenant)
	try:
		check_seat_capacity(doc.seat_limit, doc.active_students, int(adding))
	except ValueError as e:
		frappe.throw(str(e), title=_("Seat Limit"))
	return {"ok": True, "seats_available": seats_available(doc.seat_limit, doc.active_students)}


def mark_provisioning(subdomain: str, notes: str | None = None):
	"""Called by the provisioning CLI when it starts work."""
	doc = frappe.get_doc("LMS Tenant", subdomain)
	doc.provisioning_notes = notes
	doc.transition_to("Provisioning")


def mark_provisioned(subdomain: str, notes: str | None = None):
	"""Called by the provisioning CLI once the site is live."""
	doc = frappe.get_doc("LMS Tenant", subdomain)
	doc.provisioned_at = now_datetime()
	doc.provisioning_notes = notes
	doc.transition_to("Active")
