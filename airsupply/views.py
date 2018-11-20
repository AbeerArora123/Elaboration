from django.views import generic
from django.shortcuts import render, redirect
from django.template.defaulttags import register
from .models import Item, Category, Order, LineItem, Cart, DroneLoad, Place
from django.http import JsonResponse
import csv
from django.contrib.auth import authenticate, login
from django.views.generic import View
from .forms import UserForm
from django.utils.encoding import force_bytes, force_text
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from .tokens import account_activation_token
from django.contrib.auth.models import User
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.contrib.sites.shortcuts import get_current_site
from django.urls import reverse

from django.http import HttpResponse

#debugging:
import logging
logger = logging.getLogger(__name__)

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)


class BrowseView(generic.ListView):
    template_name = 'clinic-manager/browse.html'
    context_object_name = 'all_items'

    if len(Cart.objects.all()) == 0: #not identifying with user for now
        Cart.objects.create_cart()

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


class CartView(generic.ListView):
    template_name = 'clinic-manager/cart.html'
    context_object_name = 'all_items'

    def get_queryset(self):
        return Cart.objects.get(status=Order.CART).items.all()

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


def cart_add(request):# in first iteration, no clinic manager so we get the one available cart

    try:
        itemID = request.GET.get('itemid', 0)

        item = Item.objects.get(pk=itemID)
        quantity = request.GET.get('qty', 0)

    except(KeyError, Item.DoesNotExist):
        return JsonResponse({'success': False, 'error_message': 'Item does not exist'})
    else:
        cart = Cart.objects.get(status=Order.CART) # get any cart

        newItem = LineItem()
        newItem.item = item
        newItem.quantity = quantity
        if cart.checkTotalWeight(newItem):
            newItem.save()
            cart.addLineItem(newItem)
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'error_message': 'Cart weight limit exceeded'})


class OrderView(generic.ListView):
    template_name = 'clinic-manager/view-orders.html'
    context_object_name = 'all_orders'

    def get_queryset(self):
        return Order.objects.exclude(status=Order.CART)


def cart_checkout(request):# in first iteration, no clinic manager so we get the one available cart
    priority = request.POST['priority']

    cart = Cart.objects.get(status=Order.CART)
    if cart.checkout(priority):
        return redirect('airsupply:my_orders')
    else:
        return JsonResponse({'success': False})


class OrderDetailView(generic.ListView):
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


class DispatchView(generic.ListView):
    template_name = 'dispatcher/dispatch-queue.html'
    context_object_name = 'all_droneloads'

    def get_queryset(self):
        return DroneLoad.objects.exclude(dispatched=DroneLoad.TRUE)

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


def get_itinerary(request, pk):
    # Create the HttpResponse object with the appropriate CSV header.
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="itinerary_005.csv"'
    writer = csv.writer(response)
    writer.writerow(['22.266040', '113.997882', '17'])
    writer.writerow(['22.265040', '113.927482', '5'])
    writer.writerow(['22.236040', '113.947882', '1'])
    writer.writerow(['22.265040', '113.995182', '10'])
    writer.writerow(['22.170257', '114.131376', '161'])

    return response


def dispatch(request, pk):
    dl = DroneLoad.objects.get(pk=pk)
    dl.dispatch()
    return redirect('airsupply:dispatch_view')


def forgot_password(request):
    return render(request, 'forgot-password.html', {})


class PriorityQueueView(generic.ListView):
    template_name = 'warehouse-personnel/priority-queue.html'
    context_object_name = 'all_orders'

    def get_queryset(self):
        order = {
            "High": 1,
            "Medium": 2,
            "Low": 3
        }
        orderedList = sorted(Order.objects.filter(status="Queued for Processing"), key=lambda n: (order[n.priority], n.timeOrdered))
        return orderedList


def order_processed(request, pk):
    logger.error("Removing Order!!!")
    order = Order.objects.get(pk=pk)
    order.update_status("Queued for Dispatch")
    return redirect('airsupply:priority_queue')


class UserFormView(View):
    form_class = UserForm
    template_name = 'register.html'

    def get(self, request):
        form = self.form_class(None)
        if self.processToken(request, request.GET.get('uidb64'), request.GET.get('token')):
            return render(request, self.template_name, {'form': form})
        else:
            return HttpResponse("Verification link is invalid!!")

    def post(self, request):
        form = self.form_class(request.POST)

        if form.is_valid():

            # get new user details ie. firstname, lastname, username, password (if cm then clinicname too). save the new details (overwrite username/password)
            # login(request, user) and then redirect according to group (cm -> main/browse; dispatcher -> main/dispatch; wp -> main/priority_queue)
            user = form.save(commit=False)

            # cleaned (normalized) data
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']

            #changing user password:
            user.set_password(password)
            user.save()

            #returns User objects if credentials are correct
            user = authenticate(username=username, password=password)

            if user is not None:
                if user.is_active:
                    login(request, user)
                    return redirect('airsupply:browse')

        return render(request, self.template_name, {'form': form})

    def processToken(self, request, uidb64, token):
        try:
            uid = force_text(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
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


#login classview/functionview. similar to post of userformview



def send_activation_link(request, user):
    current_site = get_current_site(request)
    message = render_to_string('acc_active_email.html', {
        'user': user, 'domain': current_site.domain,
        'uid': urlsafe_base64_encode(force_bytes(user.pk)),
        'token': account_activation_token.make_token(user),
    })
    # Sending activation link in terminal
    # user.email_user(subject, message)
    mail_subject = 'Activate your Air Supply Pilot account.'
    to_email = user.email
    email = EmailMessage(mail_subject, message, to=[to_email])
    email.send()
