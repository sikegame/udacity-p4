App Engine application for the Udacity training course.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.

## Design Decisions

Session and Speaker models were created with reusability and accessibility in mind.

Session entity has eight properties: name, speakers, highlights, session date, session time, duration, session type and location. For the session date and time fields, I used DateProperty and TimeProperty respectively because they make query easier. The speaker field can store multiple speakers, and it takes Speaker object key (speaker's email address) as an identifier.

Speaker entity has four properties: name, bio, company, email address. It takes the speaker's email address as an entity key because it is unique to each speaker. I made the independent Speaker model because it allows users to reuse speaker's information instead of just storing a name in a Session object.

## My Approach to "Before 7, No Workshop" Problem

In order to solve the Task 3 problem, I implemented the two-step solution. First, retrieve any sessions that hold before 7 pm, and then filter out those sessions with Workshop type. This looks redundant but can circumvent the App Engine's query restriction.

## Additional Queries (Task 3)
`nonWorkshopBeforeSeven`

This query type takes websafeConferenceKey and returns all the sessions that hold before 7 pm and are not Keynote.

`getSessionByDuration`

This query type takes websafeConferenceKey and maximum duration (in minute) and returns all the sessions with the duration of less than the maximum minutes given at the specific conference.

[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool
