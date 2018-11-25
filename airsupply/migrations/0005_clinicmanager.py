# Generated by Django 2.1.2 on 2018-11-21 04:43

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('airsupply', '0004_auto_20181112_1144'),
    ]

    operations = [
        migrations.CreateModel(
            name='ClinicManager',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('clinic', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='airsupply.Place')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]