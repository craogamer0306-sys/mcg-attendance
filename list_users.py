from app import app, db, User
with app.app_context():
    users = User.query.order_by(User.id).all()
    if not users:
        print("No users found in DB.")
    else:
        print("id | name | employee_id | email | role")
        for u in users:
            print(f"{u.id} | {u.name} | {u.employee_id} | {u.email} | {u.role}")
