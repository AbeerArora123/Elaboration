from django.views import generic
from django.shortcuts import render, redirect
from django.template.defaulttags import register
from .models import Item, Category, Order, LineItem, Cart, DroneLoad, Place, ClinicManager
from django.http import JsonResponse
import csv
from django.contrib.auth import authenticate, login, logout
from django.views.generic import View
from .forms import UserForm, ClinicManagerForm
from django.utils.encoding import force_text
from django.utils.http import urlsafe_base64_decode
from .tokens import account_activation_token
from django.contrib.auth.models import User
from .tokens import send_new_password
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.mixins import UserPassesTestMixin
from django.http import HttpResponse

#debugging:
import logging
logger = logging.getLogger(__name__)

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)


# group checkers
def cm_checker(user):
    return user.groups.filter(name='Clinic Manager') | user.is_superuser


class CMCheck(UserPassesTestMixin):
    def test_func(self):
        u = self.request.user
        return u.groups.filter(name='Clinic Manager').count() != 0 | u.is_superuser


def disp_checker(user):
    return user.groups.filter(name='Dispatcher') | user.is_superuser


class DispCheck(UserPassesTestMixin):
    def test_func(self):
        u = self.request.user
        return u.groups.filter(name='Dispatcher').count() != 0 | u.is_superuser


def wp_checker(user):
    return user.groups.filter(name='Warehouse Personnel') | user.is_superuser


class WPCheck(UserPassesTestMixin):
    def test_func(self):
        u = self.request.user
        return u.groups.filter(name='Warehouse Personnel').count() != 0 | u.is_superuser


# Clinic Manager
class BrowseView(CMCheck, generic.ListView):
    group_required = u"Professor"
    template_name = 'clinic-manager/browse.html'
    context_object_name = 'all_items'

    def get_queryset(self):
        if self.kwargs.get('catID'):
            return Item.objects.filter(category=Category.objects.get(id=self.kwargs.get('catID')))
        elif self.request.GET.get('itemDesc'):
            return Item.objects.filter(description__contains=self.request.GET.get('itemDesc'))
        else:
            return Item.objects.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cats = Category.objects.all()
        context['categories'] = cats
        catSizes = {}
        for cat in cats:
            catSizes[cat] = Item.objects.filter(category=cat).count()
        context['catSizes'] = catSizes
        return context


class CartView(CMCheck, generic.ListView):
    permission_required = 'cart.change_cart'
    template_name = 'clinic-manager/cart.html'
    context_object_name = 'all_items'

    def get_queryset(self):
        return Cart.objects.get(clinicManager=self.request.user.clinicmanager, status=Order.CART).items.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        items = Order.objects.get(status=Order.CART).items.all()
        itemWeights = {}
        sum = 0
        for item in items:
            itemWeights[item] = item.item.weight * item.quantity
            sum += itemWeights[item]
        context['weights'] = itemWeights
        context['totalWeight'] = sum
        context['ordered'] = "FALSE"
        return context


@user_passes_test(cm_checker)
def cart_add(request):

    try:
        itemID = request.GET.get('itemid', 0)

        item = Item.objects.get(pk=itemID)
        quantity = request.GET.get('qty', 0)

    except(KeyError, Item.DoesNotExist):
        return JsonResponse({'success': False, 'error_message': 'Item does not exist'})
    else:
        cart = Cart.objects.get(clinicManager=request.user.clinicmanager, status=Order.CART)

        newItem = LineItem()
        newItem.item = item
        newItem.quantity = quantity
        if cart.checkTotalWeight(newItem):
            newItem.save()
            cart.addLineItem(newItem)
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'error_message': 'Cart weight limit exceeded'})


class OrderView(CMCheck, generic.ListView):
    permission_required = 'order.delete_order'
    template_name = 'clinic-manager/view-orders.html'
    context_object_name = 'all_orders'

    def get_queryset(self):
        return Order.objects.exclude(status=Order.CART)


@user_passes_test(cm_checker)
def cart_checkout(request):

    priority = request.POST['priority']

    cart = Cart.objects.get(clinicManager=request.user.clinicmanager, status=Order.CART)
    if cart.checkout(priority):
        Cart.objects.create_cart(request.user.clinicmanager)
        return redirect('airsupply:my_orders')
    else:
        return JsonResponse({'success': False})


class OrderDetailView(CMCheck, generic.ListView):
    permission_required = 'order.change_order'
    template_name = 'clinic-manager/cart.html'
    context_object_name = 'all_items'

    def get_queryset(self):
        return Order.objects.get(id=self.kwargs.get('pk')).items.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        order = Order.objects.get(id=self.kwargs.get('pk'))
        items = order.items.all()
        itemWeights = {}
        sum = 0
        for item in items:
            itemWeights[item] = item.item.weight * item.quantity
            sum += itemWeights[item]
        context['weights'] = itemWeights
        context['totalWeight'] = sum
        context['ordered'] = "TRUE"
        context['priority'] = order.priority

        return context


# Dispatcher
class DispatchView(DispCheck, generic.ListView):
    permission_required = 'drone_load.change_order'
    template_name = 'dispatcher/dispatch-queue.html'
    context_object_name = 'all_droneloads'

    def get_queryset(self):
        order = {
            "High": 1,
            "Medium": 2,
            "Low": 3
        }
        ordered_list = sorted(Order.objects.filter(status="Queued for Dispatch"),
                             key=lambda n: (order[n.priority], n.timeOrdered))
        dlWeight = 0.0
        for order in ordered_list:
            dlWeight += float(order.totalWeight)
            #TODO Complete the logic after discussion with Saad

        return DroneLoad.objects.exclude(dispatched='TRUE')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        dLs = DroneLoad.objects.exclude(dispatched=DroneLoad.TRUE)
        dlWeights = {}
        for dl in dLs:
            sum = 0.0
            for orders in dl.orders.all():
                sum += float(orders.totalWeight)
            dlWeights[dl] = sum
        context['dlWeights'] = dlWeights
        return context


@user_passes_test(disp_checker)
def get_itinerary(request, pk):
    # Create the HttpResponse object with the appropriate CSV header.
    dl = DroneLoad.objects.get(pk=pk)
    places = dl.get_itinerary()
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="itinerary_005.csv"'
    writer = csv.writer(response)
    for place in places:
        writer.writerow([place.latitude, place.longitude, place.altitude])
    return response


@user_passes_test(disp_checker)
def dispatch(request, pk):
    dl = DroneLoad.objects.get(pk=pk)
    dl.dispatch()
    return redirect('airsupply:dispatch_view')


# Warehouse Personnel
class PriorityQueueView(WPCheck, generic.ListView):
    template_name = 'warehouse-personnel/priority-queue.html'
    context_object_name = 'all_orders'

    def get_queryset(self):
        order = {
            "High": 1,
            "Medium": 2,
            "Low": 3
        }
        orderedList = sorted(Order.objects.filter(status__in=[Order.QP, Order.PW]), key=lambda n: (order[n.priority], n.timeOrdered))
        return orderedList

@user_passes_test(wp_checker)
def order_processed(request, pk):
    order = Order.objects.get(pk=pk)
    order.update_status(Order.QD)
    return redirect('airsupply:priority_queue')

@user_passes_test(wp_checker)
def processing_order(request, pk):
    try:
        order = Order.objects.get(pk=pk)
        order.update_status("Processing by Warehouse")

    except(KeyError, Item.DoesNotExist):
        return JsonResponse({'success': False, 'error_message': 'Order does not exist'})
    else:
        return JsonResponse({'success': True})


def download_shipping(request, pk):
    try:
        order = Order.objects.get(pk=pk)
    except(KeyError, Item.DoesNotExist):
        return JsonResponse({'success': False, 'error_message': 'Order does not exist'})
    else:
        return order.download_shipping()


# User
class UserRegisterView(View):
    form_class = UserForm
    template_name = 'register.html'

    def get(self, request, usernameb64, token):
        form = self.form_class(None)
        if self.processToken(request, usernameb64, token):
            role = request.user.groups.all()[0].name
            clinics = Place.objects.exclude(name='Queen Mary Hospital Drone Port')
            if role == "Clinic Manager":
                form = ClinicManagerForm
            return render(request, self.template_name, {'form': form, 'user': request.user, 'role': role, 'possibleClinics': clinics})
        else:
            return HttpResponse("Verification link is invalid!!")

    def post(self, request):
        user = request.user

        form = self.form_class(request.POST)

        if user.groups.all()[0].name == "Clinic Manager":
            form = ClinicManagerForm(request.POST)

        if form.is_valid():

            # cleaned (normalized) data
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            fname = form.cleaned_data['first_name']
            lname = form.cleaned_data['last_name']

            #changing user password:
            user.username = username
            user.set_password(password)
            user.first_name = fname
            user.last_name = lname
            user.save()

            if user.groups.all()[0].name == "Clinic Manager":
                clinic = Place.objects.get(name=form.cleaned_data['clinicName'])
                cm = ClinicManager()
                cm.user = user
                cm.clinic = clinic
                cm.save()

            return authUser(request, username, password, self.template_name, {'form': form})
        return render(request, self.template_name, {'form': form})

    def processToken(self, request, usernameb64, token):
        try:
            username = force_text(urlsafe_base64_decode(usernameb64))
            user = User.objects.get(username=username)
        except(TypeError, ValueError, OverflowError, User.DoesNotExist):
            user = None
        if user is not None and account_activation_token.check_token(user, token):
            user.is_active = True
            user.save()
            login(request, user)
            # return redirect('home')
            return True
            # return HttpResponse('Thank you for your email confirmation. Now you can login your account.')
        else:
            return False


class UserLoginView(View):
    template_name = 'login.html'

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        username = request.POST.get('username')
        password = request.POST.get('password')
        return authUser(request, username, password, 'login.html')


class UserForgotPassword(View):
    template_name = 'forgot-password.html'

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        try:
            user = User.objects.get(username=request.POST['up'])
        except User.DoesNotExist:
            try:
                user = User.objects.get(email=request.POST['up'])
            except User.DoesNotExist:
                return render(request, self.template_name, {'error_message': 'Invalid username or email'})
        send_new_password(request, user)
        return redirect('login')


def authUser(request, username, password, temp_name, data={}):
    user = authenticate(username=username, password=password)
    if user is not None:
        if user.is_active:
            login(request, user)
            role = user.groups.all()[0].name
            if role == "Clinic Manager":
                Cart.objects.create_cart(user.clinicmanager)
                return redirect('airsupply:browse')
            elif role == "Dispatcher":
                return redirect('airsupply:dispatch_view')
            elif role == "Warehouse Personnel":
                return redirect('airsupply:priority_queue')
        else:
            data['error_message'] = 'Your account has been disabled'
            return render(request, temp_name, data)
    else:
        data['error_message'] = 'Invalid username or password'
        return render(request, temp_name, data)


def logout_user(request):
    logout(request)
    return redirect('/')
