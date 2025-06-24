<?php

use Illuminate\Support\Facades\Route;

Route::get('/', function () {
    return view('welcome'); // Главная страница (можно поменять)
});

Route::get('/contact', function () {
    return view('contact'); // Страница Контакти
});
