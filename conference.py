#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from functools import wraps
from datetime import datetime, time

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import Session
from models import SessionForm
from models import SessionForms
from models import SessionByTypeQueryForm
from models import SpeakerQueryForm
from models import SessionByDurationQueryForm
from models import Speaker
from models import SpeakerForm
from models import SpeakerForms

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

from utils import getUserId

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
MEMCACHE_FEATURED_SPEAKER_KEY = 'FEATURED_SPEAKER'
FEATURED_TPL = '%s is the featured speaker for the following sessions: %s'
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1)
)

SESSION_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1)
)

SESSION_BY_TYPE_REQUEST = endpoints.ResourceContainer(
    SessionByTypeQueryForm,
    websafeConferenceKey=messages.StringField(1)
)

WISHLIST_POST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1)
)

DURATION_POST_REQUEST = endpoints.ResourceContainer(
    SessionByDurationQueryForm,
    websafeConferenceKey=messages.StringField(1)
)

SPEAKER_POST_REQUEST = endpoints.ResourceContainer(
    SpeakerForm,
    websafeSessionKey=messages.StringField(1)
)


def authentication_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException(
                'Authentication required'
            )
        return f(*args, **kwargs)
    return decorated_function


"""
@contextmanager
def get_user_prof():
    user = endpoints.get_current_user()
    user_id = getUserId(user)
    prof = ndb.Key(Profile, user_id).get()
    try:
        yield prof
    except:
        raise
"""

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )
        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    @authentication_required
    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # get Profile from datastore
        user = endpoints.get_current_user()
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


    @endpoints.method(message_types.VoidMessage,
                      StringMessage,
                      path='filterPlayground',
                      http_method='GET',
                      name='filterPlayground')
    @authentication_required
    def filterPlayground(self, request):
        """Filter Playground"""
        #q = Conference.query()
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)
        #q = q.filter(Conference.city=="London")
        #q = q.filter(Conference.topics=="Medical Innovations")
        #q = q.filter(Conference.month==6)
        user = endpoints.get_current_user()
        user_id = getUserId(user)
        if not user_id:
            raise endpoints.NotFoundException(
                'No Data'
            )

        return StringMessage(
            data=user_id
        )


# - - - Session - - - - - - - - - - - - - - - - - - - -


    def _copySessionToForm(self, sess):
        """
        Copy relevant fields from Session to SessionForm.

        :param sess: Session object
        :return: SessionForm
        """
        # Get empty SessionForm
        sf = SessionForm()

        # Copy fields from Session to SessionForm
        for field in sf.all_fields():
            if hasattr(sess, field.name):

                # Convert data into appropriate format
                if field.name == 'sess_time':
                    setattr(sf, 'sess_time',
                            str(getattr(sess, 'sess_time'))[:5])
                elif field.name == 'sess_date':
                    setattr(sf, 'sess_date',
                            str(getattr(sess, 'sess_date'))[:10])
                else:
                    setattr(sf, field.name, getattr(sess, field.name))
        sf.check_initialized()
        return sf


    @authentication_required
    def _createSessionObject(self, request):
        """
        Create Session object (Task 1)

        :param request: websafeConferenceKey
        :return: SessionForm
        """
        # Get parent Conference entity
        c_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        conf = c_key.get()

        # Check if conf exists
        if not conf:
            raise endpoints.NotFoundException(
                'The Parent conference was not found')

        # Check if user has right permission
        user = endpoints.get_current_user()
        user_id = getUserId(user)
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'You must be the organizer of the conference')

        # Check if user filled required fields
        if not request.sess_time or \
                not request.sess_date or \
                not request.name:
            raise endpoints.BadRequestException(
                'Please fill all the required fields')

        data = {field.name: getattr(request, field.name)
                for field in request.all_fields()}

        # Remove websafeConferenceKey for consistency
        del data['websafeConferenceKey']

        # Convert data into appropriate formats
        data['sess_time'] = datetime.strptime(
            data['sess_time'][:5], '%H:%M').time()
        data['sess_date'] = datetime.strptime(
            data['sess_date'][:10], '%Y-%m-%d').date()

        # Check if session will start during the conference
        session_date = data['sess_date']
        if (conf.startDate > session_date) or \
                (conf.endDate < session_date):
            raise endpoints.BadRequestException(
                'Session must held during the conference')

        # Create unique key id
        s_id = Session.allocate_ids(size=1, parent=c_key)[0]
        s_key = ndb.Key(Session, s_id, parent=c_key)
        data['key'] = s_key

        # Put data into Session entity
        Session(**data).put()

        # Set task queue for featured speaker
        taskqueue.add(
            params={'websafeConferenceKey': request.websafeConferenceKey},
            url='/tasks/set_featured_speaker')

        return self._copySessionToForm(request)


    @endpoints.method(SESSION_GET_REQUEST,
                      SessionForms,
                      path='conference/{websafeConferenceKey}/sessions',
                      http_method='GET',
                      name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """
        Return sessions at specific conference (Task 1)

        :param request: websafeConferenceKey
        :return: sessions at specific conference
        """
        # Retrieve Session objects with specific ancestor
        sessions = Session.query(
            ancestor=ndb.Key(urlsafe=request.websafeConferenceKey)
        )\
            .order(Session.sess_date)\
            .order(Session.sess_time)

        # Check if session is not empty
        if not sessions:
            raise endpoints.NotFoundException(
                'No sessions were found for the conference')

        return SessionForms(
            sessions=[self._copySessionToForm(sess) for sess in sessions])


    @endpoints.method(SESSION_BY_TYPE_REQUEST,
                      SessionForms,
                      path='conference/{websafeConferenceKey}/type',
                      http_method='POST',
                      name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """
        Return filtered sessions by type and conference (Task 1)

        :param request: websafeConferenceKey, type of session
        :return: specific type of sessions at specific conference
        """
        # Check if user filled required fields
        if not request.sess_type:
            raise endpoints.BadRequestException(
                'Required field is missing')

        # Retrieve sessions of specific ancestry
        sessions = Session.query(
            ancestor=ndb.Key(urlsafe=request.websafeConferenceKey)
        )\
            .filter(Session.sess_type == request.sess_type)\
            .order(Session.sess_date)\
            .order(Session.sess_time)

        # Check if Session is not empty
        if not sessions:
            raise endpoints.NotFoundException(
                'No session were found')

        return SessionForms(
            sessions=[self._copySessionToForm(sess) for sess in sessions])


    @endpoints.method(SpeakerQueryForm,
                      SessionForms,
                      path='getSessionsBySpeaker',
                      http_method='POST',
                      name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """
        Return sessions of specific speaker (Task 1)

        :param request: speaker name
        :return: all sessions of specific speaker
        """
        # Check if user filled required fields
        if not request.speaker:
            raise endpoints.BadRequestException(
                'Required field is missing')

        # Retrieve sessions of specific speaker
        sessions = Session.query(Session.speakers == request.speaker)\
            .order(Session.sess_date)\
            .order(Session.sess_time)

        # Check if Session is not empty
        if not sessions:
            raise endpoints.NotFoundException(
                'No sessions were found')

        return SessionForms(
            sessions=[self._copySessionToForm(sess) for sess in sessions])


    @endpoints.method(SESSION_POST_REQUEST,
                      SessionForm,
                      path='conference/{websafeConferenceKey}/create',
                      http_method='POST',
                      name='createSession')
    def createSession(self, request):
        """
        Create new Session object and
        associate it with specific conference (Task 1)

        :param request: websafeConferenceKey
        :return: Session form
        """
        return self._createSessionObject(request)


    def _filterSessionByTime(self, wsck, stop_time, start_time=time(0, 0)):
        """
        Return sessions starting between start_time and stop_time
        (Task 3, one of two additional queries)

        :param wsck: websafeConferenceKey
        :param stop_time: filtering stop time
        :param start_time: filtering start time (Optional)
        :return: Session objects
        """
        # Filter query by filtering stop time
        step_one = Session.query(ancestor=ndb.Key(urlsafe=wsck))\
            .filter(Session.sess_time < stop_time)\
            .order(Session.sess_time)\
            .fetch()
        # Filter Session objects by filtering start time
        step_two = [sess for sess in step_one
                    if sess.sess_time >= start_time]

        return step_two


    def _filterSessionByDuration(self, wsck, duration):
        """
        Return Session objects less than maximum duration
        (Task 3, one of two additional queries)

        :param wsck: websafeConferenceKey
        :param duration: maximum duration in minutes
        :return: Session objects
        """
        # Retrieve Session objects with specific ancestry
        sessions = Session.query(ancestor=ndb.Key(urlsafe=wsck))\
            .filter(Session.duration <= duration)\
            .order(Session.duration)\
            .fetch()

        # Check if sessions is not empty
        if not sessions:
            raise endpoints.BadRequestException(
                'No sessions were returned')

        return sessions


    @endpoints.method(DURATION_POST_REQUEST,
                      SessionForms,
                      path='filter/{websafeConferenceKey}/duration',
                      http_method='POST',
                      name='getSessionByDuration')
    def getSessionByDuration(self, request):
        """
        Return sessions less than maximum duration

        :param request: websafeConferenceKey, maximum_duration
        :return: SessionForms
        """
        wsck = request.websafeConferenceKey  # Get websafeConferenceKey
        max_duration = request.max_duration  # Get max_duration

        # Retrieve Session objects with less than max duration
        sessions = self._filterSessionByDuration(wsck, max_duration)

        return SessionForms(
            sessions=[self._copySessionToForm(sess) for sess in sessions])


    @endpoints.method(SESSION_GET_REQUEST,
                      SessionForms,
                      path='filter/{websafeConferenceKey}',
                      http_method='GET',
                      name='nonWorkshopBeforeSeven')
    def nonWorkshopBeforeSeven(self, request):
        """
        Return sessions which are not Keynote and start before 7pm
        (Task 3, solve the query related problem)

        :param request: websafeConferenceKey
        :return: Session forms
        """
        TYPE_EXCLUDE = 'Workshop'
        TIME_END = '19:00'  # HH:MM format

        # Convert string into time format
        stop_time = datetime.strptime(TIME_END, '%H:%M').time()

        # Retrieve Session objects
        sessions = self._filterSessionByTime(
            request.websafeConferenceKey, stop_time)

        return SessionForms(
            sessions=[self._copySessionToForm(sess) for sess in sessions
                      if sess.sess_type != TYPE_EXCLUDE])


# - - - Wishlist - - - - - - - - - - - - - - - - - - - -


    def _sessionWishlist(self, request, add=True):
        """
        Add or remove session to user's wishlist in Profile object

        :param request: websafeSessionKey
        :param add: add if True, remove if False
        :return: retval boolean
        """
        retval = False
        s_key = request.websafeSessionKey  # Get websafeSessionKey
        prof = self._getProfileFromUser()  # Get user Profile object

        # Add websafeSessionKey to user wishlist if add=True
        if add:
            if s_key in prof.session_wishlist:
                raise endpoints.ConflictException(
                    'Session has already registered in your wishlist')
            prof.session_wishlist.append(s_key)
            retval = True

        # Remove from user wishlist if add=False
        else:
            if s_key in prof.session_wishlist:
                prof.session_wishlist.remove(s_key)
                retval = False

        # Update Profile entity
        prof.put()

        return BooleanMessage(data=retval)


    @endpoints.method(WISHLIST_POST_REQUEST,
                      BooleanMessage,
                      path='wishlist/{websafeSessionKey}',
                      http_method='POST',
                      name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """
        Add session to user's list of session wishlist (Task 2)

        :param request: websafeSessionKey
        :return: boolean message
        """
        return self._sessionWishlist(request)


    @endpoints.method(WISHLIST_POST_REQUEST,
                      BooleanMessage,
                      path='wishlist/{websafeSessionKey}',
                      http_method='DELETE',
                      name='removeSessionFromWishlist')
    def removeSessionFromWishlist(self, request):
        """
        Remove session from user's list of session wishlist (Extra work)

        :param request: websafeSessionKey
        :return: boolean message
        """
        return self._sessionWishlist(request, add=False)


    @endpoints.method(message_types.VoidMessage,
                      SessionForms,
                      path='getSessionsInWishlist',
                      http_method='GET',
                      name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """
        Query for all sessions from user Profile object (Task 2)

        :param request: None
        :return: SessionForms
        """
        prof = self._getProfileFromUser()
        s_keys = prof.session_wishlist
        sessions = [ndb.Key(urlsafe=s_key).get() for s_key in s_keys]

        return SessionForms(
            sessions=[self._copySessionToForm(sess) for sess in sessions])


# - - - Featured Speaker - - - - - - - - - - - - - - - - - - - -


    @staticmethod
    def _cacheFeaturedSpeaker(wsck):
        """
        Find featured speaker and add to Memcache entry

        :param wsck: wsck
        :return: String message
        """
        # Retrieve Session objects with specific ancestry
        sessions = Session.query(ancestor=ndb.Key(urlsafe=wsck))\
            .fetch(projection=[Session.name, Session.speakers])

        # Search for speaker with multiple sessions
        feat_speaker = ''
        speaker_container = []
        for session in sessions:
            for speaker in session.speakers:
                if speaker not in speaker_container:
                    speaker_container.append(speaker)
                else:
                    feat_speaker = speaker

        # Get a list of sessions name for featured speaker
        feat_sessions = [sess.name for sess in sessions
                         if feat_speaker in sess.speakers]

        # Add message to Memcache if featured speaker exists
        if feat_speaker:
            message = FEATURED_TPL % \
                      (feat_speaker, ', '.join(sess for sess in feat_sessions))
            memcache.set(MEMCACHE_FEATURED_SPEAKER_KEY, message)
        else:
            message = ''
            memcache.delete(MEMCACHE_FEATURED_SPEAKER_KEY)

        return message


    @endpoints.method(message_types.VoidMessage,
                      StringMessage,
                      path='getFeaturedSpeaker',
                      http_method='GET',
                      name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """
        Return featured speaker with a list of presenting sessions
        (Task 4)

        :param request: None
        :return: String massage
        """
        return StringMessage(
            data=memcache.get(MEMCACHE_FEATURED_SPEAKER_KEY) or '')


# - - - Speaker Entity - - - - - - - - - - - - - - - - - - - -


    def _copySpeakerToForm(self, speaker):
        # Get empty SessionForm
        sf = SpeakerForm()

        # Copy fields from Session to SessionForm
        for field in sf.all_fields():
            if hasattr(speaker, field.name):
                setattr(sf, field.name, getattr(speaker, field.name))
        sf.check_initialized()
        return sf


    def _createSpeakerObject(self, request):
        """
        Create Speaker entity

        :param request: websafeSessionKey
        :return: Speaker object
        """
        # Get Session key
        s_key = ndb.Key(urlsafe=request.websafeSessionKey)

        # Check if Session key exists
        if not s_key:
            raise endpoints.NotFoundException(
                'The parent session was not found')

        # Copy input into data array
        data = {field.name: getattr(request, field.name)
                for field in request.all_fields()}

        # Remove unnecessary variables
        del data['websafeSessionKey']

        # Create Speaker key associated with Session
        main_key = ndb.Key(Speaker, data['mainEmail'], parent=s_key)
        data['key'] = main_key

        # Put to the database
        Speaker(**data).put()

        return self._copySpeakerToForm(request)


    @endpoints.method(SPEAKER_POST_REQUEST,
                      SpeakerForm,
                      path='session/{websafeSessionKey}/create',
                      http_method='POST',
                      name='createSpeaker')
    def createSpeaker(self, request):
        """
        Create Speaker entity associated with Session

        :param request: websafeSessionKey
        :return: SpeakerForm
        """
        return self._createSpeakerObject(request)


api = endpoints.api_server([ConferenceApi])  # register API
