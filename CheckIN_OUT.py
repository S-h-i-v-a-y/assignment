from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from neo4j import GraphDatabase
from datetime import datetime

app = FastAPI()

# Neo4j Database connection
class Neo4jDatabase:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver("neo4j://localhost:7687", auth=("neo4j", "intel2011"))

    def close(self):
        self.driver.close()

    def set_organization_times(self, org_id, opening_time, closing_time):
        with self.driver.session() as session:
            session.run(
                """
                MATCH (org:Organization {id: $org_id})
                SET org.opening_time = $opening_time, org.closing_time = $closing_time
                RETURN org
                """, org_id=org_id, opening_time=opening_time, closing_time=closing_time
            )

    def check_in_user(self, user_id, org_id):
        with self.driver.session() as session:
            # Fetch organization's operating hours
            result = session.run(
                """
                MATCH (org:Organization {id: $org_id})
                RETURN org.opening_time AS opening_time, org.closing_time AS closing_time
                """, org_id=org_id
            ).single()

            if not result:
                raise Exception("Organization not found")

            opening_time = result["opening_time"]
            closing_time = result["closing_time"]
            current_time = datetime.now().strftime("%H:%M")

            # Check if current time is within operating hours
            if not (opening_time <= current_time <= closing_time):
                raise Exception(f"Organization is closed. Operating hours are {opening_time} to {closing_time}")

            # Create CHECKED_IN relationship
            result = session.run(
                """
                MATCH (u:Person {id: $user_id}), (org:Organization {id: $org_id})
                MERGE (u)-[:CHECKED_IN]->(org)
                RETURN u
                """, user_id=user_id, org_id=org_id
            )
            return result.single()

db = Neo4jDatabase(uri="bolt://localhost:7687", user="neo4j", password="password")

# Pydantic models
class OrganizationTimes(BaseModel):
    org_id: int
    opening_time: str
    closing_time: str

class CheckInRequest(BaseModel):
    user_id: int
    org_id: int

@app.post("/organization/set-times")
async def set_times(times: OrganizationTimes):
    """
    Set opening and closing times for the organization.
    """
    try:
        db.set_organization_times(times.org_id, times.opening_time, times.closing_time)
        return {"message": f"Opening and closing times set to {times.opening_time} and {times.closing_time}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

#-----------------------------------------------------------------------------------------------------

@app.post("/organization/checkin")
async def check_in_user(request: CheckInRequest):
    """
    Check in a user to the organization, verifying operating hours.
    """
    try:
        with db.driver.session() as session:
            # Fetch organization's opening and closing times
            result = session.run(
                """
                MATCH (org:Organization {id: $org_id})
                RETURN org.opening_time AS opening_time, org.closing_time AS closing_time
                """, org_id=request.org_id
            ).single()

            if not result:
                raise HTTPException(status_code=404, detail="Organization not found")

            opening_time = result["opening_time"]
            closing_time = result["closing_time"]

            # Validate that opening_time and closing_time are set
            if opening_time is None or closing_time is None:
                raise HTTPException(
                    status_code=400,
                    detail="Organization's opening and closing times are not set"
                )

            # Get current time
            current_time = datetime.now().strftime("%H:%M")

            # Convert times to datetime objects for proper comparison
            current_time_dt = datetime.strptime(current_time, "%H:%M")
            opening_time_dt = datetime.strptime(opening_time, "%H:%M")
            closing_time_dt = datetime.strptime(closing_time, "%H:%M")

            # Validate current time against opening and closing times
            if not (opening_time_dt <= current_time_dt <= closing_time_dt):
                raise HTTPException(
                    status_code=403,
                    detail=f"Organization is closed. Operating hours are {opening_time} to {closing_time}"
                )

            # Create CHECKED_IN relationship if within operating hours
            result = session.run(
                """
                MATCH (u:Person {id: $user_id}), (org:Organization {id: $org_id})
                MERGE (u)-[:CHECKED_IN]->(org)
                RETURN u
                """, user_id=request.user_id, org_id=request.org_id
            )
            if not result:
                raise HTTPException(status_code=404, detail="User not found")

        return {"message": "User successfully checked in"}

    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

#-------------------------------------------------------------------------------------------------
@app.get("/organization/active-users")
async def get_active_users(org_id: int):
    """
    Retrieve the list of active users who have checked in to the organization, grouped by their roles,
    and update status based on the organization's opening and closing times.
    :param org_id: The organization ID to retrieve active users.
    """
    try:
        with db.driver.session() as session:
            # Fetch organization's opening and closing times
            times_result = session.run(
                """
                MATCH (org:Organization {id: $org_id})
                RETURN org.opening_time AS opening_time, org.closing_time AS closing_time
                """, org_id=org_id
            ).single()

            if not times_result:
                raise HTTPException(status_code=404, detail="Organization not found")

            opening_time = times_result["opening_time"]
            closing_time = times_result["closing_time"]

            # Validate that opening_time and closing_time are set
            if opening_time is None or closing_time is None:
                raise HTTPException(
                    status_code=400,
                    detail="Organization's opening and closing times are not set"
                )

            # Get current time
            current_time = datetime.now().strftime("%H:%M")

            # Convert times to datetime objects for proper comparison
            current_time_dt = datetime.strptime(current_time, "%H:%M")
            opening_time_dt = datetime.strptime(opening_time, "%H:%M")
            closing_time_dt = datetime.strptime(closing_time, "%H:%M")

            # Logic based on current time
            if opening_time_dt <= current_time_dt <= closing_time_dt:
                # Within operating hours, fetch all active users
                active_result = session.run(
                    """
                    MATCH (u:Person)-[:CHECKED_IN]->(org:Organization {id: $org_id})
                    RETURN u.role AS role, collect({id: u.id, name: u.name}) AS users
                    """, org_id=org_id
                )
            else:
                # After closing hours, fetch only updated active users (e.g., admin stays active)
                active_result = session.run(
                    """
                    MATCH (u:Person)-[:CHECKED_IN]->(org:Organization {id: $org_id})
                    WHERE u.role = 'admin'
                    RETURN u.role AS role, collect({id: u.id, name: u.name}) AS users
                    """, org_id=org_id
                )

            # Process the results into active users grouped by their role
            active_users = [{"role": record["role"], "users": record["users"]} for record in active_result]

            # If no active users are found, return a 404 error
            if not active_users:
                raise HTTPException(status_code=404, detail="No active users found in the organization")

            return {"active_users": active_users}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



#--------------------------------------------------------------------------------------------------
@app.post("/organization/auto-checkout")
async def auto_checkout(org_id: int):
    """
    Automatically check out all users from the organization after closing time, except the admin.
    :param org_id: The organization ID for automatic checkout.
    """
    try:
        with db.driver.session() as session:
            # Fetch the organization's closing time
            result = session.run(
                """
                MATCH (org:Organization {id: $org_id})
                RETURN org.closing_time AS closing_time
                """, {"org_id": org_id}  # Explicitly pass org_id as a parameter
            ).single()

            if not result:
                raise HTTPException(status_code=404, detail="Organization not found")

            closing_time = result["closing_time"]

            # Validate that closing_time is set
            if closing_time is None:
                raise HTTPException(
                    status_code=400,
                    detail="Organization's closing time is not set"
                )

            # Get current time in "HH:MM" format
            current_time = datetime.now().strftime("%H:%M")

            # Convert times to datetime objects for proper comparison
            current_time_dt = datetime.strptime(current_time, "%H:%M")
            closing_time_dt = datetime.strptime(closing_time, "%H:%M")

            # Perform checkout only if current time is past the closing time
            if current_time_dt > closing_time_dt:
                # Remove CHECKED_IN relationships for all users except admin
                session.run(
                    """
                    MATCH (u:Person)-[r:CHECKED_IN]->(org:Organization {id: $org_id})
                    WHERE u.role <> 'admin'
                    DELETE r
                    """, {"org_id": org_id}  # Explicitly pass org_id as a parameter
                )
                return {"message": "All non-admin users have been checked out after closing time"}
            else:
                return {"message": "It's not past the organization's closing time yet"}

    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



#--------------------------------------------------------------------------------------------------
@app.post("/organization/admin-checkout")
async def admin_checkout(org_id: int):
    """
    Check out the admin from the organization.
    :param org_id: The organization ID for the admin checkout.
    """
    try:
        with db.driver.session() as session:
            # Fetch admin's CHECKED_IN relationship
            result = session.run(
                """
                MATCH (admin:Person {role: 'admin'})-[r:CHECKED_IN]->(org:Organization {id: $org_id})
                DELETE r
                RETURN admin
                """, {"org_id": org_id}  # Explicitly pass org_id as a parameter
            )

            # Validate if admin was checked out
            if not result.single():
                raise HTTPException(
                    status_code=404,
                    detail="Admin was not checked in or the organization does not exist"
                )

        return {"message": "Admin has been successfully checked out from the organization"}

    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.on_event("shutdown")
def shutdown_event():
    db.close()