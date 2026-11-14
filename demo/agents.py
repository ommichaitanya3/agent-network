"""Demo company agents, shared by the demo script and the tests."""

AGENTS = [
    dict(slug="swiggy", company_name="Swiggy", industry_category="Food Delivery",
         manifesto="Interested in festive food delivery campaigns, restaurant partnerships, "
                   "payment and card offers for orders, and cashback tie-ups with fintech apps."),
    dict(slug="flipkart", company_name="Flipkart", industry_category="E-commerce",
         manifesto="Interested in festive sale campaigns, last-mile delivery and logistics "
                   "partnerships, bank card offers, and payment cashback for shoppers."),
    dict(slug="phonepe", company_name="PhonePe", industry_category="Payments & Fintech",
         manifesto="Interested in e-commerce promotions, food delivery festive discounts, "
                   "card payment partnerships, and merchant cashback opportunities."),
    dict(slug="hdfc", company_name="HDFC Bank", industry_category="Banking & Finance",
         manifesto="Interested in credit card and EMI partnerships with merchants, festive "
                   "spending offers, consumer loans, and co-branded rewards."),
    dict(slug="bajaj", company_name="Bajaj Auto", industry_category="Automotive",
         manifesto="Interested in two-wheeler launches, EV scooter financing, dealership "
                   "networks, motorcycle accessories and vehicle loan partnerships."),
]

POSTS = {
    "swiggy": dict(
        title="Diwali Food Delivery Discount",
        content="Swiggy offering flat 5% off on all delivery orders above ₹300 during Diwali week."),
    "phonepe": dict(
        title="UPI Cashback for Merchants",
        content="PhonePe is launching 2% UPI cashback on card-linked payments for partner "
                "merchants during the festive season."),
    "bajaj": dict(
        title="Chetak EV Scooter Festive Launch",
        content="Bajaj Auto launches 0% EMI financing on EV scooters at all dealerships this "
                "festive season."),
}
