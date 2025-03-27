from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from neo4j import GraphDatabase

app = FastAPI()

# Neo4j database connection
class Neo4jDatabase:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver("neo4j://localhost:7687", auth=("neo4j", "intel2011"))

    def close(self):
        self.driver.close()

    def check_in_user(self, user_id, org_id):
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (u:Person {id: $user_id}), (o:Organization {id: $org_id})
                MERGE (u)-[r:CHECKED_IN]->(o)
                RETURN r
                """, user_id=user_id, org_id=org_id
            )
            return result.single()

db = Neo4jDatabase(uri="bolt://localhost:7687", user="neo4j", password="password")


class CheckInRequest(BaseModel):
    user_id: int
    org_id: int

@app.post("/checkin")
async def check_in(request: list[CheckInRequest]):
    """
    Check in multiple users into the organization, one by one.
    :param request: A list of CheckInRequest objects with user_id and org_id.
    """
    checkin_responses = []
    try:
        for user in request:
            result = db.check_in_user(user.user_id, user.org_id)
            if not result:
                checkin_responses.append({"user_id": user.user_id, "status": "User or Organization not found"})
            else:
                checkin_responses.append({"user_id": user.user_id, "status": "Checked in successfully"})
        return {"results": checkin_responses}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/checkin/active-users")
async def get_all_active_users():
    """
    Retrieve all active users who explicitly checked in, grouped by their roles.
    """
    try:
        with db.driver.session() as session:
            # Query to fetch only users with an explicit CHECKED_IN relationship
            result = session.run(
                """
                MATCH (u:Person)-[:CHECKED_IN]->(:Organization)
                RETURN u.role AS role, collect({id: u.id, name: u.name}) AS users
                """
            )


            active_users = [{"role": record["role"], "users": record["users"]} for record in result]


            if not active_users:
                raise HTTPException(status_code=404, detail="No active users found")

            return {"active_users": active_users}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
#----------------------------------------------------------------------------------------------
@app.post("/checkout")
async def checkout_users(org_id: int):
    """
    Check out all users except admin from the organization.
    :param org_id: The organization ID to check out users.
    """
    try:
        with db.driver.session() as session:
            # Remove CHECKED_IN relationships for all users except admin
            session.run(
                """
                MATCH (u:Person)-[r:CHECKED_IN]->(o:Organization {id: $org_id})
                WHERE u.role <> 'admin'
                DELETE r
                """, org_id=org_id
            )
            return {"message": "All non-admin users have been checked out"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/checkout/admin")
async def checkout_admin(org_id: int):
    """
    Check out the admin from the organization.
    :param org_id: The organization ID to check out the admin.
    """
    try:
        with db.driver.session() as session:
            # Remove CHECKED_IN relationship for the admin
            session.run(
                """
                MATCH (u:Person {role: 'admin'})-[r:CHECKED_IN]->(o:Organization {id: $org_id})
                DELETE r
                """, org_id=org_id
            )
            return {"message": "Admin has been checked out"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.on_event("shutdown")
def shutdown_event():
    db.close()


