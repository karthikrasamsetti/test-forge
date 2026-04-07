# SauceDemo Test Cases

## TC001 — Login with valid credentials

**Category:** authentication
**Priority:** high
**URL:** https://www.saucedemo.com
**Preconditions:** User account standard_user exists in system

**Steps:**
1. Navigate to https://www.saucedemo.com
2. Enter username standard_user
3. Enter password secret_sauce
4. Click the Login button

**Expected:** User is redirected to the products inventory page

---

## TC002 — Login with locked out user

**Category:** authentication
**Priority:** high
**URL:** https://www.saucedemo.com
**Preconditions:** locked_out_user account exists in system

**Steps:**
1. Navigate to https://www.saucedemo.com
2. Enter username locked_out_user
3. Enter password secret_sauce
4. Click the Login button

**Expected:** Error message displayed saying user has been locked out

---

## TC003 — Add product to cart

**Category:** cart
**Priority:** medium
**URL:** https://www.saucedemo.com/inventory.html
**Preconditions:** User is logged in as standard_user

**Steps:**
1. Click Add to cart button on Sauce Labs Backpack
2. Observe the cart icon in the top right

**Expected:** Cart icon shows badge with number 1
